#!/usr/bin/env python3
"""世界の主要経済ニュース・ダイジェスト

前日 00:00 から当日 05:00（既定はJST、月曜日は先週金曜 00:00 から）までに公開されたニュースを
RSS/Atom フィードから集め、Claude で重要度を判断して日本語のレポートにまとめる。

使い方:
    python econ_digest.py                      # 今日の 05:00 を終点に実行
    python econ_digest.py --date 2026-11-26    # 指定日の 05:00 を終点に実行
    python econ_digest.py --dry-run            # 収集だけしてClaudeは呼ばない
    python econ_digest.py --web-search         # RSSに加えてWeb検索でも補完
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import json
import re
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import feedparser

BASE_DIR = Path(__file__).resolve().parent
MODEL = "claude-opus-5-5"
USER_AGENT = "Mozilla/5.0 (compatible; econ-digest/1.0)"


@dataclass
class Article:
    source: str
    region: str
    title: str
    summary: str
    link: str
    published: datetime  # aware datetime

    def key(self) -> str:
        norm = re.sub(r"\W+", "", self.title.lower())
        return hashlib.sha1(norm.encode()).hexdigest()


# ---------------------------------------------------------------- 期間計算

def compute_window(target: date, tz: ZoneInfo, start_hour: int = 0, end_hour: int = 5,
                   days_back: int | None = None):
    """[start, end) を返す。

    通常は「前日 start_hour 時 〜 当日 end_hour 時」。
    月曜日は週末をまとめて「先週金曜 start_hour 時 〜 月曜 end_hour 時」にする。
    days_back を指定するとその日数だけさかのぼる。
    """
    if days_back is None:
        days_back = 3 if target.weekday() == 0 else 1  # 0 = 月曜日
    start = datetime.combine(target - timedelta(days=days_back), time(start_hour), tzinfo=tz)
    end = datetime.combine(target, time(end_hour), tzinfo=tz)
    return start, end


_WHEN_RE = re.compile(r"when(:|%3A)\d+d")


def expand_feed(feed: dict, start: datetime, end: datetime) -> list[dict]:
    """Google News 検索は1回で最大100件しか返らないため、期間が1日半を超えるとき
    （月曜日など）は `when:Nd` を1日ごとの `after:/before:` 検索に分割する。"""
    m = _WHEN_RE.search(feed["url"])
    if not m or end - start <= timedelta(days=1, hours=12):
        return [feed]
    sep = m.group(1)
    first = start.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    out, d = [], first
    while d <= last:
        nxt = d + timedelta(days=1)
        url = feed["url"].replace(m.group(0), f"after{sep}{d}+before{sep}{nxt}")
        out.append(feed | {"url": url, "label": f"{feed['name']} ({d:%m/%d})"})
        d = nxt
    return out


# ---------------------------------------------------------------- 収集

def _clean(text: str, limit: int = 400) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:limit]


def _entry_time(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(attr)
        if st:
            # feedparser は UTC の struct_time を返す
            return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    return None


def fetch_feed(feed: dict) -> tuple[dict, list[Article], str | None]:
    name = feed["name"]
    try:
        parsed = feedparser.parse(feed["url"], agent=USER_AGENT)
    except Exception as e:  # ネットワーク例外など
        return feed, [], str(e)
    if parsed.get("bozo") and not parsed.entries:
        return feed, [], str(parsed.get("bozo_exception", "parse error"))
    # feeds.toml の任意設定: require（タイトルが一致必須の正規表現）/ exclude（一致したら除外）
    require = re.compile(feed["require"]) if feed.get("require") else None
    exclude = re.compile(feed["exclude"]) if feed.get("exclude") else None
    out = []
    for e in parsed.entries:
        ts = _entry_time(e)
        if ts is None:
            continue
        title = e.get("title", "")
        if (require and not require.search(title)) or (exclude and exclude.search(title)):
            continue
        out.append(Article(
            source=name,
            region=feed.get("region", ""),
            title=_clean(e.get("title", ""), 300),
            summary=_clean(e.get("summary", "")),
            link=e.get("link", ""),
            published=ts,
        ))
    return feed, out, None


def collect(feeds: list[dict], start: datetime, end: datetime, verbose=True) -> list[Article]:
    articles: dict[str, Article] = {}
    jobs = [f for feed in feeds for f in expand_feed(feed, start, end)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for feed, items, err in pool.map(fetch_feed, jobs):
            name = feed.get("label", feed["name"])
            if err:
                print(f"  [warn] {name}: {err}", file=sys.stderr)
            hits = [a for a in items if start <= a.published < end]
            if verbose:
                print(f"  {name}: {len(items)}件取得 / 期間内 {len(hits)}件", file=sys.stderr)
            if items and "label" not in feed:  # 日ごとに分割した検索では鮮度チェックをしない
                newest = max(a.published for a in items)
                oldest = min(a.published for a in items)
                if newest < end - timedelta(days=3):
                    print(f"  [warn] {name}: 最新記事が {newest:%Y-%m-%d} で、更新が止まっている可能性があります",
                          file=sys.stderr)
                elif oldest > start:
                    print(f"  [info] {name}: 最古の記事が {oldest.astimezone(start.tzinfo):%m/%d %H:%M} のため、"
                          "期間の前半を取りこぼしている可能性があります（05時台の実行を推奨）", file=sys.stderr)
            for a in hits:
                articles.setdefault(a.key(), a)  # タイトル重複を除去
    return sorted(articles.values(), key=lambda a: a.published)


# ---------------------------------------------------------------- 要約

SYSTEM_PROMPT = """あなたは国際金融市場を担当するベテランのエコノミスト兼編集者です。
与えられたニュース見出しの一覧から、指定期間内に世界で起きた「主要な経済事件」を選び、
日本の投資家・ビジネスパーソン向けに日本語で簡潔なブリーフィングを作成してください。

方針:
- 重要度は市場・実体経済への影響の大きさで判断する（中央銀行の政策決定、主要経済指標、
  為替・株・債券・商品の大きな変動、大型M&A・倒産、通商・制裁・財政政策、地政学リスクなど）。
- 同じ出来事を報じた複数の記事は1件にまとめる。
- 一覧にない事実を付け加えたり、数値を推測したりしない。不明な点は書かない。
- 時刻は指定タイムゾーンで表記する。
- 各項目の末尾に根拠となった記事のURLを1〜2件、Markdownリンクで付ける。

出力形式（Markdown）:
# 世界経済ダイジェスト（{期間}）
## 要点（3〜5行）
## 主要トピック
### 1. 見出し  — 地域 / 分野 / 重要度(★1〜3)
本文2〜4文。市場への影響があれば一言。 [出典](URL)
（重要度の高い順に最大15件）
## 地域別の短信
米国・欧州・日本・中国/アジア・その他 ごとに、主要トピックに入らなかった項目を箇条書きで。
## 今後の注目点
"""


def build_prompt(articles: list[Article], start: datetime, end: datetime, tzname: str) -> str:
    rows = []
    for i, a in enumerate(articles, 1):
        local = a.published.astimezone(start.tzinfo).strftime("%m/%d %H:%M")
        line = f"[{i}] {local} | {a.source} | {a.title}"
        if a.summary and a.summary != a.title:
            line += f"\n    {a.summary}"
        line += f"\n    {a.link}"
        rows.append(line)
    period = f"{start:%Y-%m-%d %H:%M} 〜 {end:%Y-%m-%d %H:%M} ({tzname})"
    return (
        f"対象期間: {period}\n"
        f"記事数: {len(articles)}\n\n"
        "<articles>\n" + "\n".join(rows) + "\n</articles>\n\n"
        "上記からブリーフィングを作成してください。"
    )


def summarize(articles: list[Article], start: datetime, end: datetime, tzname: str,
              web_search: bool = False, effort: str = "medium") -> str:
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY などから認証情報を解決
    prompt = build_prompt(articles, start, end, tzname)
    tools = []
    if web_search:
        prompt += (
            "\n\n記事一覧で不足している主要な出来事があれば web_search で補完してください。"
            "ただし対象期間内に起きたことが確認できたものだけを含めること。"
        )
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]

    messages = [{"role": "user", "content": prompt}]
    kwargs = dict(
        model=MODEL,
        max_tokens=64000,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # 安全分類器による拒否時は自動で別モデルに切替
    )
    if tools:
        kwargs["tools"] = tools

    # web_search 利用時は pause_turn で一時停止することがあるので継続する
    for _ in range(5):
        with client.beta.messages.stream(messages=messages, **kwargs) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "pause_turn":
            messages = [messages[0], {"role": "assistant", "content": msg.content}]
            continue
        break

    if msg.stop_reason == "refusal":
        cat = msg.stop_details.category if msg.stop_details else None
        raise RuntimeError(f"Claude が要約を拒否しました (category={cat})")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if msg.stop_reason == "max_tokens":
        text += "\n\n> ※出力上限に達したため途中で切れています。"
    return text


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="前日0時〜当日5時の世界の主要経済ニュースを要約")
    p.add_argument("--date", type=date.fromisoformat, help="当日の日付 (YYYY-MM-DD)。既定は今日")
    p.add_argument("--tz", default="Asia/Tokyo", help="基準タイムゾーン (既定: Asia/Tokyo)")
    p.add_argument("--end-hour", type=int, default=5, help="当日の終了時刻 (既定: 5)")
    p.add_argument("--days-back", type=int, default=None,
                   help="何日前の0時からにするか (既定: 月曜は3=先週金曜、それ以外は1=前日)")
    p.add_argument("--feeds", type=Path, default=BASE_DIR / "feeds.toml")
    p.add_argument("--out-dir", type=Path, default=BASE_DIR / "reports")
    p.add_argument("--web-search", action="store_true", help="Claude の Web 検索で補完")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--dry-run", action="store_true", help="収集結果のみ保存し要約しない")
    args = p.parse_args(argv)

    tz = ZoneInfo(args.tz)
    target = args.date or datetime.now(tz).date()
    start, end = compute_window(target, tz, end_hour=args.end_hour, days_back=args.days_back)
    print(f"対象期間: {start.isoformat()} 〜 {end.isoformat()}", file=sys.stderr)

    feeds = tomllib.loads(args.feeds.read_text(encoding="utf-8"))["feeds"]
    articles = collect(feeds, start, end)
    print(f"期間内の記事: {len(articles)}件（重複除去後）", file=sys.stderr)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.out_dir / f"{target}_articles.json"
    raw_path.write_text(json.dumps(
        [asdict(a) | {"published": a.published.isoformat()} for a in articles],
        ensure_ascii=False, indent=1), encoding="utf-8")

    if args.dry_run:
        print(f"収集結果: {raw_path}", file=sys.stderr)
        return 0
    if not articles and not args.web_search:
        print("期間内の記事がありません。フィード設定やネットワークを確認してください。", file=sys.stderr)
        return 1

    try:
        report = summarize(articles, start, end, args.tz, args.web_search, args.effort)
    except anthropic.AuthenticationError:
        print("認証エラー: ANTHROPIC_API_KEY を設定してください。", file=sys.stderr)
        return 2
    except anthropic.RateLimitError:
        print("レート制限に達しました。しばらくして再実行してください。", file=sys.stderr)
        return 3
    except anthropic.APIStatusError as e:
        print(f"API エラー ({e.status_code}): {e.message}", file=sys.stderr)
        return 4
    except anthropic.APIConnectionError:
        print("API に接続できません。ネットワークを確認してください。", file=sys.stderr)
        return 5

    out_path = args.out_dir / f"{target}.md"
    out_path.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\n保存先: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
