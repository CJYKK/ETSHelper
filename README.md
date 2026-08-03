# ETSHelper (E听说试题助手)

ETSHelper 是针对 Windows 平台“E听说”桌面客户端开发的数据分析与辅助工具。通过实时监测本地试题库缓存，提取各题型的标准答案与解析文本，并提供窗口吸附与悬浮显示支持。

---

## 核心特性

- **数据目录实时监测**
  自动追踪 `%APPDATA%\ETS` 下的试题缓存目录。当客户端下载新试题集或更新内容时，自动读取并同步渲染最新数据。

- **全题型答案解析**
  覆盖试题集内全部 13 个大题：
  - 听后选择 (1~9): 提取题目选项与标准选项字母
  - 听后记录填空 (10): 提取完整填空词汇列表
  - 看图/故事复述 (11): 提取完整参考范文文本
  - 短文朗读 (12): 标注大题类型
  - 听后回答问答 (13): 提取问答各子题的标准参考答案

- **双模式视图与窗口吸附**
  - **完整大模式**: 提供试题集历史列表与详细的题目/选项/答案预览。
  - **极简悬浮模式**: 切换为无边框置顶 HUD 面板，自动吸附在 `ETSShell.exe` 主窗口右侧随动。支持跟随主窗口进行最小化隐藏与还原，并在主窗口最大化时自动靠右上角对齐。

- **零外部运行库依赖**
  界面与窗口逻辑完全基于 Python 标准库 (`tkinter`, `ctypes`, `threading`, `json`) 实现，运行占用低。

---

## 使用说明

### 1. 运行预编译程序
直接运行打包生成的 `ETSHelper.exe` 即可。

### 2. 源码运行
需要 Python 3.10+ 环境：

```bash
uv run python app.py
```

---

## 单文件打包

项目使用 PyInstaller 打包为免安装单文件 EXE：

```bash
uv run pyinstaller --onefile --noconsole --name "ETSHelper" app.py
```

打包产物位于 `dist/ETSHelper.exe`。

---

## 技术架构

```
ETSHelper/
├── app.py              # 主程序 (GUI, Win32 API 窗口吸附, 数据提取引擎)
├── pyproject.toml      # 项目配置与依赖说明
└── README.md
```

- **数据提取引擎 (`ETSExtractor`)**: 解析缓存目录下的 `info.json` 与 `content2.json` / `content.json`，完成 HTML 标签清洗与 Schema 变体兼容。
- **吸附引擎 (`Win32DockEngine`)**: 通过 Win32 API (`EnumWindows`, `QueryFullProcessImageNameW`, `GetWindowRect`) 精准锁定 `ETSShell.exe` 并计算相对坐标。
