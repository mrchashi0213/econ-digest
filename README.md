# 世界経済ダイジェスト

前日 00:00 〜 当日 05:00（JST）に世界で起きた主要な経済ニュースを集めて、日本語のレポートにまとめます。前日が土日・祝日・年末年始（12/31〜1/3）のときは、直近の営業日の 00:00 からが対象です（月曜や祝日明けは先週金曜から、など）。

## 仕組み
1. `feeds.toml` に書いたRSS/Atomフィード（NHK、BBC、CNBC、MarketWatch、Google Newsなど）を並列に取得します
2. 公開時刻が対象期間 `[前日00:00, 当日05:00)`（前日が休みなら `[直近の営業日00:00, 当日05:00)`）に入る記事だけを残し、タイトルが同じ記事は1件にまとめます
   - Google News は1回の検索で最大100件しか返らないため、月曜や祝日明けなど期間が1日半を超えるときは1日ごとに分けて検索します
   - `--days-back N` で、N日前の0時からに変更できます
3. Claude（`claude-opus-5-5`）が重要度を判断し、要点・主要トピック（★付き）・地域別の短信・今後の注目点をMarkdownで出力します
4. `reports/YYYY-MM-DD.md` にレポート、`reports/YYYY-MM-DD_articles.json` に収集した記事を保存します

## セットアップ
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## 使い方
```bash
python econ_digest.py                    # 今日分
python econ_digest.py --date 2026-11-26  # 日付を指定
python econ_digest.py --web-search       # RSSで拾えない出来事をWeb検索で補う
python econ_digest.py --dry-run          # 収集だけ（APIは呼ばない）
python econ_digest.py --tz America/New_York --end-hour 6
```

## 毎朝自動で動かす（cron の例: 毎日 05:05 JST）
```
CRON_TZ=Asia/Tokyo
5 5 * * * cd /path/to/econ_digest && /usr/bin/python3 econ_digest.py >> run.log 2>&1
```

## 毎朝の自動実行（Claude のクラウド）
Claude の定期実行タスクが平日の毎朝 5:05（日本時間）にこのリポジトリを取得し、[`DAILY_TASK.md`](DAILY_TASK.md) の手順で動きます。

- 記事を収集し（`--dry-run`、APIキー不要）、要約はそのセッションの Claude が行います
- `reports/YYYY-MM-DD.md`（レポート）と `reports/YYYY-MM-DD_articles.json`（収集した記事）を main に push します
- スライド（claude.ai のアーティファクト）をその日の内容で作り直します

祝日・年末年始（12/31〜1/3）の朝は `--skip-holiday` により何もせず終了します。

フィードを変えるなら `feeds.toml`、手順を変えるなら `DAILY_TASK.md` を編集してください。翌朝から反映されます。
