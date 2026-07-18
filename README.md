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

フォーム入力、画像配置、PC／スマホプレビュー、下書き保存、ZIP書き出し、サイトへの追加を一画面で行えます。

```sh
python tools/article_studio.py
```

ブラウザで `http://127.0.0.1:8770/` を開きます。「サイトへ追加」は記事HTML、画像、`data/articles.json`をローカルへ原子的に反映します。GitHub Pagesへ公開するときは、追加後の変更をcommitして`main`へ反映します。

Windowsでは、リポジトリ直下の `START_ARTICLE_STUDIO.cmd` をダブルクリックしても起動できます。

下書きはリポジトリ内の `.article-studio/drafts/` に保存され、Git管理から除外されます。

#### Xアカウントから下書きを作る

記事スタジオ右上の「Xから作成」では、Xのユーザー名（`@username`）またはプロフィールURLから画像付き投稿を取得できます。

1. X Developer Portalで作成したアプリのBearer Tokenを入力する
2. ユーザー名またはプロフィールURLを指定して「公開投稿を取得」する
3. 記事に載せる投稿を1〜6件選び、一覧サムネイルに使う本人画像を1枚選ぶ
4. 「選択した投稿で下書きを作る」を押し、タイトル・本文・画像を確認する
5. 画像利用の権利、出典、18歳以上向け表現、安全条件を確認してからサイトへ追加する

Bearer Tokenはブラウザ、下書き、記事パッケージへ保存されません。起動前に環境変数 `X_BEARER_TOKEN` を設定して、画面入力を省略することもできます。

取得対象は公開アカウントの画像付きオリジナル投稿です。非公開アカウント、返信、リポスト、動画だけの投稿は対象外です。選択した本人画像はローカルの記事画像として保存し、ホームと記事一覧のサムネイルに使います。本文では投稿者名、ユーザー名、投稿文、日時、元投稿へのリンクを含むX公式埋め込みを使用します。

X APIの利用料金や表示要件は変更されることがあります。運用前にX Developer Platformの最新の料金、表示要件、開発者ポリシーを確認してください。X APIや公式埋め込みを使っても第三者コンテンツの利用権が自動で付与されるわけではありません。必要な許可を取得し、削除済み投稿や権利者から要請された画像は速やかに取り下げてください。

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
