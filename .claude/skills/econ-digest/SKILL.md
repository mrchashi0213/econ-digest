---
name: econ-digest
description: 世界経済ダイジェスト（mrchashi0213/econ-digest）の運用・改修ガイド。前日0時〜当日5時（日本時間、休み明けは直近の営業日0時から）の世界の主要経済ニュースをRSSで集め、日本語のMarkdownレポートとスライドにまとめる仕組みについて、毎朝の定期実行、フィードの追加・除外、対象期間や祝日の扱い、スライドの更新、トラブル対応を扱う。このリポジトリのコード・feeds.toml・DAILY_TASK.md・定期実行タスク・スライドを触るとき、または「経済ダイジェスト」「毎朝のレポート」「スライドを更新」と言われたときに使う。
---

# 世界経済ダイジェスト

世界の主要な経済ニュースを毎朝自動で集め、米国と日本を厚めにした日本語のレポートとスライドにまとめる仕組み。

## 全体像

| 要素 | 場所 | 役割 |
|---|---|---|
| 収集プログラム | `econ_digest.py` | RSS/Atom を並列取得し、対象期間で絞り込み、タイトル重複を除いて `reports/YYYY-MM-DD_articles.json` に保存 |
| フィード設定 | `feeds.toml` | 取得先の一覧。フィードごとに `require` / `exclude`（タイトルの正規表現）を設定できる |
| 毎朝の手順書 | `DAILY_TASK.md` | 定期実行のセッションが従う手順（収集 → レポート → スライド → push → 表示） |
| レポート | `reports/YYYY-MM-DD.md` | 日本語のMarkdown。出典リンク付き |
| スライド | https://claude.ai/artifact/PrXTb7UgxSu6VyUphGRjeU | Slides 型のアーティファクト。毎朝同じURLを作り直す。共有設定はユーザーが管理（Claudeは変更しない） |
| 定期実行タスク | `trig_01LDt2qTan1R1C8kqnLPY9tP`「世界経済ダイジェスト（平日の朝5:05）」 | `CRON_TZ=Asia/Tokyo 5 5 * * 1-5`。新しいセッションではなく、元の会話セッション `session_01GPFyj6Zt4AeLX77jLGkiYZ` に指示が届く |

要約は Claude API を呼ばず、定期実行で起動したセッションの Claude が `--dry-run` の収集結果を読んで書く（APIキー不要）。`econ_digest.py` には API で要約する機能（`claude-opus-5-5`、`fallbacks="default"`）も残してあり、`--dry-run` を外せば使える。

## 対象期間のルール

- 終了は常に当日 05:00（日本時間）。
- 開始は「当日より前で直近の営業日」の 00:00。
  - 平日の翌朝：前日 00:00 から
  - 月曜：先週金曜 00:00 から
  - 祝日明け：直近の営業日 00:00 から（例：2026/11/24（火）は 11/20（金）から、2027/1/4（月）は 12/30（水）から）
- 休みとして扱う日：土日、祝日（`jpholiday`。振替休日・国民の休日を含む）、年末年始（12/31〜1/3）。`is_business_day()` で判定する。
- `--days-back N` を指定すると、営業日に関係なく N 日前の 00:00 からになる。
- `--skip-holiday` を付けると、当日が休日なら `SKIP: ...` と表示して終了コード 10 で終わる。定期実行はこれを使い、祝日・年末年始の朝は何もしない（土日はスケジュール側で起動しない）。

## Google News の扱い

- 日経とロイター日本語版には公式のRSSがない（ロイターは2020年に終了）。そこで Google News のサイト指定検索で取得している：`site:nikkei.com`、`site:reuters.com`（`hl=ja&gl=JP&ceid=JP:ja`）。`site:jp.reuters.com` では0件になる。
- Google News の検索は1回で最大100件しか返らない。期間が1日半を超える日（月曜・休み明け）は、`expand_feed()` が `when:Nd` を1日ごとの `after:YYYY-MM-DD+before:YYYY-MM-DD` 検索に分割する。平日は分割しない。
- ロイターの検索結果には株価ページ（`CUPu.CXX - Reuters`、`| Stock Price & Latest News`）が混じる。`require = "[ぁ-んァ-ヶ一-龠]"` と `exclude = "Stock Price|Latest News"` で除外している。
- 「Google News: 経済」には Yahoo!ファイナンスの株価ページが大量に混じる。`exclude = "株価・株式情報|株価チャート"` で除外している。
- 検索結果は時間とともに入れ替わる。そのため収集する時刻が遅いほど、期間の前半を取りこぼす。05時台に実行するのが前提。

## フィードの現状（feeds.toml）

使用中：BBC Business、MarketWatch（`feeds.content.dowjones.io` に移転済み）、Al Jazeera、Google News（英語・日本語）、日経（経済・市場／全般）、ロイター日本語版（経済・市場／全般）。

無効化して理由をコメントに残しているもの：
- NHK 経済 `cat5.xml`：2026年8月以降、更新が止まっている
- CNBC：クラウド環境からのアクセスを拒否される（Access Denied）

フィードを追加するときは次の順で進める。
1. `python econ_digest.py --dry-run --out-dir <作業用フォルダ>` で件数と中身を確認する（`reports/` を上書きしないよう別フォルダに出す）
2. 不要なページが混じるなら `exclude` を足す
3. 新しいホストは、クラウド環境のネットワーク許可リストに追加する必要がある（下記）

## クラウド環境のネットワーク

Default 環境の許可ホスト：`www3.nhk.or.jp`、`feeds.bbci.co.uk`、`www.cnbc.com`、`feeds.marketwatch.com`、`feeds.content.dowjones.io`、`www.aljazeera.com`、`news.google.com`。
プロキシに 403（`agent proxy: no rule or network setting allows this host`）で止められたら、`get_environment` で現状を確認してから `update_environment` でホストを追加する（ユーザーの承認が必要）。

## 毎朝の作業（DAILY_TASK.md の要約）

1. `pip install -q -r requirements.txt` → `python econ_digest.py --dry-run --skip-holiday`（終了コード 10 なら休日なので、「休日のため実行しませんでした」と一言だけ報告して終了）
2. `articles.json` を全件読み、主要な出来事を選ぶ。米国と日本を各2つ（①景気・物価・金融政策、②市場・企業・通商/政策）、そのほかに世界、今後の注目点。見出しと要旨にある事実だけを書き、数値は推測しない。日付のはっきりしない数値は載せない。その日の articles.json にない話題は使わない（前日の記事は検索結果から消えていることがある）
3. `reports/YYYY-MM-DD.md` を書く。出典リンクは articles.json の `link` からタイトル検索で差し込むと確実
4. スライドを更新する（下記）
5. `reports/` を main にコミットして push する
6. Artifact の `open` でスライドを表示する
7. 記事件数・主なトピック3つを短く報告する。記事が20件未満なら、取得状況を報告してスライドもレポートも更新しない

## スライドの更新

- 先に Artifact の `read`（`paths` で `project/deck.json` と `project/slides/*.html`）を行い、保存された場所を `root` にして Write し、publish する。セッションが再起動すると作業用フォルダのパスが変わるので、前回のパスに頼らない。
- 7枚の構成：`cover` / `us-macro` / `us-market` / `jp-macro` / `jp-market` / `world` / `watch`。デザインは `project/deck.css` のクラスをそのまま使い、文言だけを差し替える。
  - `us-macro` / `jp-macro`：`.stat`（大きな数字）＋ `.list` の `.item` 4つ
  - `us-market`：`.cards3` の `.card` 3つ（tag / h3 / desc / kicker）
  - `jp-market`：`.grid6` の `.block` 6つ（tag / h3 / desc は1行）
  - `world`：`.rows` の `.row` 6つ（region / text）
  - `watch`：`.wgrid` の `.witem` 6つ
- `deck.json` の `title` を「世界経済ダイジェスト YYYY-MM-DD」にし、表紙の期間・記事数・情報源数を実際の値にする。
- はみ出しを防ぐ目安：各行の文字数を今のスライドと同じくらいに収める。`.stat .num` は140pxなので、6文字以上の数字（例 `2.645%`）は `style="font-size:104px"` を付ける。
- 各スライドの最後に、話す内容のメモ（`<aside>`）を残す。

## 経緯と注意点（つまずいた点）

- **毎朝新しいセッションを起動する方式は失敗した**：定期実行で毎回新しく作られるセッションでは `add_repo` が使えず、リポジトリを取得できなかった。そのため、元の会話セッションに指示が届く方式（`persistent_session_id`）にしている。このセッションをアーカイブ・削除すると、定期実行も止まる。
- **定期実行タスクの削除**：自動の権限チェックで拒否されることがある。その場合はまず `enabled=false` で止め、ユーザーが明示的に依頼したときに削除する。
- **GitHub の push**：`git -c push.negotiate=true push origin main`。コミットには会話の指示にある署名（Co-Authored-By / Claude-Session）を付ける。
- **収集件数の目安**：平日の朝は約300〜400件。月曜・休み明けは1,000件を超えることがあり、ゴールデンウィークや年末年始の明けは数千件になる見込み。
- **日付の扱い**：`published` の時刻はUTCで保存されている（日本時間 = UTC+9）。日付のはっきりしない見出しの数値（例：「日経平均終値1633円安」が何日の終値か）は載せない。

## よく使うコマンド

```bash
python econ_digest.py --dry-run                     # 今日分を収集（要約しない）
python econ_digest.py --dry-run --skip-holiday      # 休日なら終了コード10
python econ_digest.py --dry-run --date 2026-11-30   # 日付を指定
python econ_digest.py --dry-run --days-back 3       # 3日前の0時から
python econ_digest.py                               # APIで要約まで（ANTHROPIC_API_KEY が必要）
```
