# KsText Publisher

一个独立小工具，用来维护 `KsText` 仓库。

功能：

- 左侧直接列出全部文本包
- 右侧可编辑每个包的 `ID / 名字 / 作者 / 简介 / 语言`
- 保存后会直接回写对应的 `packs/*.json`
- 扫描 `packs/*.json`
- 自动重建 `index.json`
- 作者为空时自动写成 `佚名`
- 自动生成 `entryCount`
- 自动生成 `sha256`
- 自动生成 `downloadUrl`
- 可选地对变化包自动升级 `version`
- 一键 `git commit + push`

## 启动

双击：

```text
launch_kstext_publisher.bat
```

或者命令行：

```powershell
python kstext_publisher.py --gui
```

## 命令行模式

只扫描预览：

```powershell
python kstext_publisher.py --repo E:\DESKTOP\project\KsText_shell --dry-run
```

重建 `index.json`：

```powershell
python kstext_publisher.py --repo E:\DESKTOP\project\KsText_shell --write-index
```

一键重建并推送：

```powershell
python kstext_publisher.py --repo E:\DESKTOP\project\KsText_shell --commit --push
```

## GUI 流程

1. 点 `扫描仓库`
2. 左侧点一个包
3. 右侧改 `ID / 名字 / 作者 / 简介`
4. 点 `保存当前元数据` 或 `保存全部元数据`
5. 点 `重建 index.json`
6. 最后点 `提交并推送` 或 `一键发布`
