# Streamlit 製 Abaqus 入力ファイル生成アプリ

## 1. アプリ概要
- テンプレートとなる Abaqus の `.inp` ファイルをアップロードし、任意のテキストを置換することで複数の入力ファイルを自動生成する Streamlit アプリです。
- 置換対象と置換後テキストの候補を複数登録し、全組み合わせから必要なものだけを選んで生成できます。

## 2. 実行環境
- Python 3.x
- Streamlit

## 3. セットアップ手順
1. 仮想環境を作成します。
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows の場合は .venv\Scripts\activate
   ```
2. 依存パッケージをインストールします。
   ```bash
   pip install streamlit
   ```

## 4. 起動方法
```bash
streamlit run scripts/app1-input.py
```

## 5. 使用手順
1. **(Ⅰ) テンプレートとなる inp ファイルのアップロード**
   - Streamlit の画面から `.inp` ファイルをアップロードします。
   - ファイルはアプリ内で文字列として保持され、後続の置換処理に利用されます。
2. **(Ⅱ) 置換対象の入力**
   - `st.text_area` で置換したいテキストを入力します。デフォルトで 1 つの入力欄が表示され、「追加」ボタンで複数の置換対象を追加できます。
   - `st.radio` で「完全一致／部分一致」を切り替えられます（デフォルトは完全一致）。
   - 一致箇所が複数ある場合はすべて置換されます。
3. **(Ⅲ) 置換後テキストの入力**
   - 各置換対象に対して、`st.text_area` で置換後テキストの候補を入力します。
   - 「追加」ボタンで候補を増やし、①、②、③…のように番号を振って管理します。
4. **(Ⅳ) 置換の組み合わせ設定と出力**
   - 置換対象と置換後テキスト候補の全組み合わせが自動生成され、`st.dataframe` に一覧表示されます。
   - 各組み合わせの左側のチェックボックスで生成対象を選択し、「Generate inputs」ボタンを押すと選択した `.inp` ファイルが出力されます。

## 6. 出力仕様
- 生成されたファイルはアップロードしたテンプレートファイルと同じディレクトリに保存されます。
- ファイル名は `<テンプレート名>_(組み合わせ番号).inp` の形式です。
  - 例：`template_(1-1).inp`、`template_(1-2).inp`、`template_(2-1-3).inp`

## 7. 注意事項
- 置換対象がテンプレート内に必ず存在することを事前に確認してください。
- 完全一致／部分一致の検索方式を切り替え可能です。
- 一致箇所が複数ある場合はすべて置換されます。

## 8. Abaqus 解析 (②) と結果抽出 (③) の連続実行
`workflow.py` で、すでに生成済みの `.inp` ファイルに対して ② の解析と ③ の結果抽出を一括実行できます。従来通り ② と ③ は個別でも実行可能です。解析対象となる `.inp` の一覧は一括実行の開始時に一度だけ確定するため、解析完了後にファイル構成が変わっても同じ対象に対して抽出が走ります。

### 8.1 Streamlit から ②→③ をまとめて実行する
ボタンひとつで ② と ③ を連続実行したい場合は、以下の Streamlit アプリを利用できます（① は含みません）。

```bash
streamlit run workflow_app.py
```

1. `.inp` フォルダと抽出結果の保存先フォルダを入力します。
2. 対象にする `.inp` をチェックボックスで選択します（初期状態では全選択）。
3. 解析コマンドと抽出コマンドのテンプレートを確認・編集します。
4. 「② 解析のみ実行」「③ 抽出のみ実行」「②→③ 一括実行」のいずれかのボタンを押します。

### 8.2 CLI で ②→③ をまとめて実行する
フォルダ内のすべての `.inp` を解析してから抽出する場合:

```bash
python workflow.py --jobs-dir jobs --results-dir extracts run-and-extract \
  --abaqus-template "abaqus job={job_name} input={inp_path}" \
  --extract-template "python extract_results.py --odb {odb_path} --out {results_dir}"
```

特定の入力 (`case1.inp`, `case2.inp`) だけを対象にする場合は `--targets` を指定してください:

```bash
python workflow.py --jobs-dir jobs --results-dir extracts \
  --targets case1 case2 run-and-extract
```

解析だけ、または抽出だけを行いたい場合はそれぞれ `run`、`extract` サブコマンドを使用します。
