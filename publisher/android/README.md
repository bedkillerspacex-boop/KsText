# KsText Publisher Android

这是 `tools/kstext_publisher` 的安卓版本工程。

当前实现目标：

- 支持 Android 6.0 起步，也就是 `minSdk 23`
- `targetSdk` / `compileSdk` 对齐到当前稳定的 Android 16，也就是 `API 36`
- 在 App 内同步 `KsText` 仓库缓存
- 编辑 `packs/*.json`
- 本地重建 `index.json`
- 通过 GitHub API 把变更发布回仓库

## 为什么安卓版不直接内置 git

桌面版是本地 `git clone + commit + push`。

安卓上如果继续完全照搬，会遇到几类问题：

- 不同厂商 ROM 对外部存储和可执行文件限制很多
- 没有稳定可依赖的系统 `git`
- 为了覆盖 Android 6 到新版本，直接依赖 GitHub HTTPS API 会更稳

所以这个版本选择了：

- 同步阶段：读取 GitHub 仓库 `packs/` 和 `index.json`
- 发布阶段：把本地改动通过 GitHub Contents API 写回目标分支

这和桌面版的“维护 KsText 仓库”目标一致，但底层实现更适合安卓。

## 打开方式

用 Android Studio 单独打开：

`tools/kstext_publisher_android`

## 当前状态

这是第一版安卓工程骨架，已经包含：

- 仓库配置页
- 包列表
- 元数据和 entries 编辑区
- 新建包
- 重建 `index.json`
- 发布到 GitHub

## 仍建议后续补强

- Token 本地加密存储
- 更细的 GitHub API 错误提示
- 发布前 diff 预览
- 批量多文件单提交
- 更完整的 UI 自适应与横竖屏布局
