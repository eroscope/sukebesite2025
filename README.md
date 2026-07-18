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
