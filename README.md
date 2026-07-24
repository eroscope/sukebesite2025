# 淫談屋

## Windows版 記事編集室

`START_INDANYA_APP.cmd` をダブルクリックすると、ブラウザではなくWindowsアプリとして記事編集室が開きます。

- `URLから作成`: URLを貼り、Codexへ素材選定・タイトル・レス構成・下書き保存をまとめて任せる
- `記事下書き` / `記事編集`: 生成済み記事の確認、編集、動画を含むプレビュー
- `許可管理`: 素材の許可状況、連絡先、メモを管理
- `管理サイト`: 公開URLを開く、サイトの追加・編集・切り替え

配布用アプリは `BUILD_INDANYA_APP.cmd` で作成できます。完成した実行ファイルは `dist/IndanyaStudio/IndanyaStudio.exe` です。

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

URLを1件入力するだけで、素材回収、広告除外、本編画像・動画の選定、Codexによる2ちゃん風記事の生成、下書き保存、プレビューまで連続実行します。生成後は素材や設定を変更した再生成、本文編集、許可管理、ZIP書き出し、サイトへの追加を行えます。Codexは記事スタジオの裏側で実行されるため、記事ごとにCodexアプリを開く必要はありません。

FANZA作品または関連レビュー記事のURLでは、ページ内の作品リンクや品番を確認してPR用の商品カードを追加できます。設定画面に自分のDMMアフィリエイトIDを保存すると、自動巡回で作る関連記事を含めてFANZAへの誘導リンクへ反映されます。

```sh
python tools/article_studio.py
```

ブラウザで `http://127.0.0.1:8770/` を開きます。「サイトへ追加」は記事HTML、画像、`data/articles.json`をローカルへ原子的に反映します。GitHub Pagesへ公開するときは、追加後の変更をcommitして`main`へ反映します。

Windowsでは、リポジトリ直下の `START_ARTICLE_STUDIO.cmd` をダブルクリックしても起動できます。

下書きと生成ジョブはリポジトリ内の `.article-studio/` に保存され、Git管理から除外されます。記事スタジオはCodexデスクトップアプリの保存済みログインとローカルCLIを自動検出します。

#### URLから下書きを作る

1. 最初の画面で、Webページ、Xプロフィール、X投稿、動画ページのURLを1件貼る
2. 「Codexに全部任せて作る」を押す
3. 生成された記事、Codexが選んだ本編画像・動画、プレビューを確認する
4. 必要な場合だけ素材や生成設定を変更し、「選択内容で作り直す」を押す
4. 生成状況が100%になると、自動保存された下書きとプレビューが開く
5. 「許可管理」で未連絡、依頼済み、許可済み、使用不可を更新する
6. 内容、出典、18歳以上向け表現、安全条件を確認してからサイトへ追加する

一般ページはHTMLメタ情報、本文候補、公開画像を解析します。Xプロフィールは最新投稿の公式タイムライン、X投稿は公式埋め込みを記事へ組み込みます。ページから画像を取得できない場合は、手元の画像を1枚追加して下書きを生成できます。URL解析と下書き生成にX API Bearer Tokenは不要です。

生成したレスは編集用の再構成文として保存されます。Codexの利用上限に達した場合は、画面にエラーが表示されるので、時間を置いて「もう一度実行」を押します。元ページ由来の情報、画像、動画は公開前に内容と利用許可を確認し、削除・変更の依頼があった場合は記事側も更新してください。

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
