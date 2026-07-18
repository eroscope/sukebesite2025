# 淫談屋 自動記事受け入れ仕様

## 受け入れ口

公開サイトはGitHub Pagesの静的サイトです。公開URLにPOST APIは置きません。将来の自動記事ツールは、このリポジトリへ記事パッケージを追加してcommit/pushします。APIキーやGitHubトークンはリポジトリへ保存しません。

## 入力パッケージ

```text
generated-article/
├─ metadata.json
├─ article.html
└─ images/
   ├─ image-01.webp
   └─ image-02.webp
```

`metadata.json`は`schemas/articles.schema.json`に従います。`slug`、`url`、`thumbnail`は同じ記事を指し、`images_used`は`images/`の実ファイル数と一致させます。記事HTMLでは、利用可能な記事画像を重複なくすべて参照します。

## 受け入れ処理

`tools/add_article.py`は次を検証してから更新します。

1. メタデータの型、必須項目、URL、slug、ローカルパス
2. 同じidとslugの対応関係
3. 画像数、拡張子、ファイル名、サムネイル
4. 記事HTMLがすべての画像を一度ずつ参照していること
5. 記事画像が外部URLやdata URLではないこと

検証後、記事HTML内の画像パスを`../assets/articles/<slug>/<filename>`へ正規化し、記事・画像・`data/articles.json`をステージングしてから置換します。途中で失敗した場合は、置換前のファイルへ戻します。

## ホーム表示

`index.html`は`data/articles.json`を読み、次の条件で表示します。

- `status`が`published`の記事だけを表示
- `published_at`の降順
- `featured: true`を注目・メイン記事に使用。なければ最新記事を使用
- 人気記事はコメント数の降順
- JSONの取得・検証に失敗した場合はHTML内の静的表示を維持
- 値はHTML文字列として挿入せず、安全なDOM APIで設定

## 公開前の除外条件

- 未成年、未成年に見える人物、年齢が確認できない性的コンテンツ
- 非同意、盗撮、流出、リベンジポルノ等の疑いがある素材
- 個人情報、住所、車両番号などが残る素材
- 利用条件を確認できない画像
- 本人の身元を画像から推測・特定する内容
- 架空レスを実在レスとして扱う内容

元情報と`source_url`を保存し、架空レスを使った場合は記事末尾へ明記します。
