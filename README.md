# 淫談屋

画像を中心に、短いレスの流れで読める静的まとめサイトです。

- 公開先: https://eroscope.github.io/sukebesite2025/
- ホーム: `index.html`
- 記事: `articles/<slug>.html`
- 記事一覧: `data/articles.json`
- 記事画像: `assets/articles/<slug>/`

## ローカル確認

リポジトリのルートでHTTPサーバーを起動します。

```sh
python -m http.server 8000
```

`http://127.0.0.1:8000/` を開いて確認します。`index.html`を直接開いた場合はJSONを取得できないため、静的フォールバック表示になります。

## 記事パッケージの追加

### 記事スタジオ

URL解析、画像候補の選択、2ちゃん風記事の下書き生成、編集、PC／スマホプレビュー、ZIP書き出し、サイトへの追加を行えます。管理画面には下書き・許可管理・公開管理・情報源・自動巡回の導線があり、現在はURLから下書きを作る工程まで動作します。

```sh
python tools/article_studio.py
```

ブラウザで `http://127.0.0.1:8770/` を開きます。「サイトへ追加」は記事HTML、画像、`data/articles.json`をローカルへ原子的に反映します。GitHub Pagesへ公開するときは、追加後の変更をcommitして`main`へ反映します。

Windowsでは、リポジトリ直下の `START_ARTICLE_STUDIO.cmd` をダブルクリックしても起動できます。

下書きはリポジトリ内の `.article-studio/drafts/` に保存され、Git管理から除外されます。

#### URLから下書きを作る

1. 最初の画面で、Webページ、Xプロフィール、X投稿、動画ページのURLを1件貼る
2. 「URLを解析」を押し、取得されたタイトル、概要、画像候補を確認する
3. 記事に使う画像を最大8枚選び、「記事下書きを生成」を押す
4. 自動保存された下書きを編集し、プレビューを更新する
5. 利用権、出典、18歳以上向け表現、安全条件を確認してからサイトへ追加する

一般ページはHTMLメタ情報、本文候補、公開画像を解析します。Xプロフィールは最新投稿の公式タイムライン、X投稿は公式埋め込みを記事へ組み込みます。ページから画像を取得できない場合は、手元の画像を1枚追加して下書きを生成できます。URL解析と下書き生成にX API Bearer Tokenは不要です。

生成したレスは編集用の再構成文として保存されます。元ページ由来の情報、画像、動画は公開前に内容と利用許可を確認し、削除・変更の依頼があった場合は記事側も更新してください。

### 記事パッケージを直接追加

推奨パッケージ構成:

```text
generated-article/
├─ metadata.json
├─ article.html
└─ images/
   ├─ image-01.webp
   └─ image-02.webp
```

まず変更なしで検証します。

```sh
python tools/add_article.py generated-article/metadata.json generated-article/article.html --dry-run
```

検証後に追加または更新します。

```sh
python tools/add_article.py generated-article/metadata.json generated-article/article.html
```

詳しい契約と安全条件は [AUTOMATION.md](AUTOMATION.md) を参照してください。
