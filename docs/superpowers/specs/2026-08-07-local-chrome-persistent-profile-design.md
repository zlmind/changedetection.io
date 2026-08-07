# Windows Local Chrome 持久 Profile 设计

**日期：** 2026-08-07  
**状态：** 已批准  
**目标版本：** 第一阶段（Windows 本地单用户）

## 1. 背景

changedetection.io 当前的浏览器抓取方式依赖远程 Selenium 或远程 Playwright。每次抓取会创建新的浏览器会话或隔离 Context，因此不会继承用户日常浏览器的 Cookie、Local Storage 和登录状态。

对于京东等需要登录或安全验证的网站，服务器浏览器和临时浏览器会话无法可靠获得用户已经完成的登录状态。

本设计新增一个完全独立的本地浏览器后端。changedetection.io 在 Windows 上启动一个可见、常驻、使用专用持久 Profile 的 Google Chrome。用户在该浏览器中手动登录一次，后续 Watch 复用相同登录状态。

## 2. 已确认决策

- 第一阶段只支持 Windows 10/11。
- changedetection.io 通过 `python changedetection.py` 直接运行，不支持本功能运行在 Docker 中。
- 第一阶段只支持系统安装的 Google Chrome。
- 使用 changedetection.io 专用 Profile，不读取或占用用户日常 Chrome Profile。
- Chrome 在 changedetection.io 运行期间保持可见和常驻。
- 所有本地浏览器检查严格串行执行。
- 每次检查新建一个标签页，正常完成后只关闭该标签页。
- 通用检测登录页、验证码和安全验证，不增加京东专用规则。
- 检测到需要人工操作时保留标签页、暂停本地浏览器任务队列，并提示用户处理。
- 不自动填写密码，不保存网站账号密码，不绕过验证码。
- 新功能必须与现有 HTTP、Selenium 和远程 Playwright 后端分离。

## 3. 目标

1. 用户登录一次后，后续检查复用专用 Chrome Profile 中的登录状态。
2. changedetection.io 重启后，Profile 中的 Cookie 和 Local Storage 继续存在。
3. 用户能够在可见 Chrome 中处理扫码、短信、滑块或其他安全验证。
4. Browser Steps、截图、XPath 和页面内容提取可用于新后端。
5. 新功能关闭时不启动额外浏览器进程。
6. 新功能故障时不自动回退到其他抓取器。
7. 不改变任何现有抓取器的配置和生命周期。

## 4. 非目标

第一阶段不实现：

- 服务端多租户访问客户本地浏览器。
- Chrome 扩展或桌面连接器。
- Docker 容器控制 Windows 宿主机 Chrome。
- Edge、Firefox、Linux 或 macOS 支持。
- 多标签页并行检查。
- 日常 Chrome Profile 导入、复制或直接复用。
- Cookie 导出、同步或独立加密存储。
- 自动输入用户名、密码、短信验证码或扫码结果。
- 验证码识别、绕过或反自动化规避。
- 京东或其他网站的专用登录规则。

## 5. 与现有后端的隔离

新增独立 Fetcher 名称：

```text
html_local_chrome
```

界面名称：

```text
Local Chrome — Persistent Profile
```

以下现有配置不参与本地浏览器功能：

```text
WEBDRIVER_URL
PLAYWRIGHT_DRIVER_URL
CHROME_OPTIONS
Extra Browsers
```

现有文件 `content_fetchers/webdriver_selenium.py` 和 `content_fetchers/playwright.py` 不增加本地模式分支，不修改其 Context、Session 或 Browser 清理行为。

新 Fetcher 可以复用现有的无状态底层能力，例如截图函数、Browser Steps 动作接口、XPath 提取脚本和库存检测脚本，但本地浏览器的连接、Context 选择和生命周期必须位于新模块中。

建议模块边界：

```text
changedetectionio/content_fetchers/local_chrome.py
changedetectionio/local_browser/manager.py
changedetectionio/local_browser/task_gate.py
changedetectionio/local_browser/auth_detector.py
```

## 6. 总体架构

```text
Scheduler / Manual Recheck
          |
          v
resolve_content_fetcher
          |
          v
html_local_chrome
          |
          v
LocalBrowserTaskGate
          |
          v
LocalChromeManager.ensure_running()
          |
          v
Playwright connect_over_cdp(loopback)
          |
          v
Persistent default browser context
          |
          v
New task page -> navigate -> Browser Steps -> extract
          |
          +---- success --------> close task page -> release gate
          |
          +---- auth challenge -> keep page -> ATTENTION_REQUIRED
```

## 7. LocalChromeManager

`LocalChromeManager` 是 changedetection.io 进程内唯一的本地 Chrome 生命周期管理器。

### 7.1 Profile 位置

Profile 目录固定从现有 datastore 路径派生：

```text
%APPDATA%\changedetection.io\browser-profile\
```

该路径只作为 Chrome 的完整 `user-data-dir`，不允许用户选择日常 Chrome 的 `Default` 或 `Profile 1` 目录。

changedetection.io 不解析 Profile 文件，不读取 Cookie 数据库，也不复制日常浏览器数据。

### 7.2 Chrome 查找

第一阶段按顺序检查常见 Windows 安装位置：

- 当前用户安装目录。
- 64 位 Program Files。
- 32 位 Program Files。

用户可以在设置中填写自定义 `chrome_executable`。自定义路径必须是存在的普通文件，并且只作为可执行文件启动。

### 7.3 启动参数

Chrome 必须：

- 使用专用 `--user-data-dir`。
- 使用可见窗口。
- 将远程调试限制在 `127.0.0.1`。
- 使用动态远程调试端口。
- 不继承现有 `CHROME_OPTIONS`。

管理器从 Profile 目录中的 `DevToolsActivePort` 读取实际端口，并通过本机调试端点验证 Chrome 已就绪。

### 7.4 进程所有权

管理器只保存并操作自己启动的 Chrome PID。

停止或重启时必须同时满足：

- PID 与记录一致。
- 进程仍然存在。
- 进程可执行文件是预期的 Chrome。
- 进程命令行包含本功能的专用 `user-data-dir`。

任一检查不满足时，不结束该进程，只报告状态不一致。

不得按进程名称批量结束 Chrome，也不得删除 Chrome 锁文件。

### 7.5 生命周期

- 只有启用 Local Chrome，或存在使用 `html_local_chrome` 的 Watch 时才启动 Chrome。
- 用户手动关闭 Chrome 后，下次检查使用原 Profile 自动重启一次。
- changedetection.io 正常退出时，关闭自己确认拥有的 Chrome。
- changedetection.io 重启后重新启动 Chrome，Profile 数据保持不变。
- Chrome/Profile 启动失败时不创建临时 Profile 作为降级方案。

## 8. LocalBrowserTaskGate

所有 `html_local_chrome` 任务使用同一个进程级任务门。

### 8.1 串行规则

- 同时最多有一个普通检查或 Browser Steps 编辑会话使用本地 Chrome。
- 等待中的任务不创建标签页。
- UI 显示“等待本地浏览器”。
- 任务门必须保证异常路径也能释放。

### 8.2 人工处理状态

检测到登录或安全验证后：

- 当前标签页保留。
- Worker 结束当前执行，不长期占用线程。
- 任务门进入逻辑阻塞状态。
- 后续任务继续排队。
- 用户只能通过“已处理，重新检查”或“取消本次检查”解除状态。

第一阶段允许该状态无限期保留，不设置自动超时。

## 9. LocalChromeFetcher

### 9.1 连接和 Context

Fetcher 通过 Playwright `connect_over_cdp()` 连接 LocalChromeManager 提供的 loopback CDP 地址。

它必须使用 Chrome 已有的持久默认 Context，例如从 `browser.contexts` 选择该 Context，而不是调用 `browser.new_context()`。

### 9.2 页面所有权

每次任务：

1. 在持久 Context 中创建新 Page。
2. 通过 CDP 读取该 Page 的 Target ID。
3. 在运行时映射中记录 `watch_uuid -> target_id`。
4. 执行导航和提取。
5. 正常完成或普通错误时关闭该 Page，并删除映射。
6. 需要人工操作时保留 Page 和映射。

重新检查或取消时，Fetcher 重新连接 CDP，枚举当前 Context 的 Page，并通过 Target ID 定位保留标签页。该映射只保存在内存中；changedetection.io 正常退出会关闭自有 Chrome，因此不需要跨进程恢复暂停页面。

Fetcher 不关闭持久 Context，也不关闭 Chrome。结束 Playwright 客户端时只断开本次 CDP 通信。

### 9.3 复用能力

新 Fetcher 应复用现有无状态能力：

- Browser Steps 动作。
- 页面等待和自定义 JavaScript。
- favicon 提取。
- XPath 元素数据提取。
- 库存文本检测。
- HTML 获取。
- 截图捕获。

不得通过修改老 Playwright Fetcher 的运行分支来实现复用。

## 10. 通用登录和验证检测

`auth_detector.py` 使用多信号评分。

### 10.1 强信号

- URL 明显进入 login、signin、auth 等认证路径。
- 存在可见的密码输入框。
- 存在可见的验证码、滑块、短信验证或扫码登录组件。
- 主页面响应为 401 或 403。

### 10.2 弱信号

- 页面出现“请登录”“验证身份”“安全验证”等常见文本。
- 页面重定向到不同的认证域名。
- 页面标题或主体内容与目标明显不一致。
- 页面只有通用框架，目标筛选器不存在。

一个强信号，或两个及以上相互独立的弱信号，进入 `ATTENTION_REQUIRED`。

单个普通文本关键词不得直接触发暂停。检测结果必须记录触发原因，但日志不得包含 Cookie、认证 Header 或表单值。

## 11. 人工恢复流程

```text
ATTENTION_REQUIRED
  -> Watch 显示“需要浏览器操作”
  -> 用户点击“打开浏览器”
  -> 用户在保留标签页中完成操作
  -> 用户点击“已处理，重新检查”
  -> Fetcher 重新连接并定位保留标签页
  -> 再次运行验证检测和内容提取
```

可用操作：

- **打开浏览器：** 将 Local Chrome 窗口带到前台。
- **已处理，重新检查：** 重新验证并继续提取。
- **取消本次检查：** 关闭保留标签页并解除任务门。

如果重新检查仍命中检测规则，继续保持 `ATTENTION_REQUIRED`。

## 12. 配置和 UI

### 12.1 持久配置

第一阶段在 `datastore.data['settings']['requests']['local_chrome']` 下只增加：

```text
enabled: bool
chrome_executable: optional string
```

Profile 路径由 datastore 路径派生，不作为自由输入配置。

PID、CDP 端口、运行状态和暂停任务是运行时状态，不写入长期设置。

### 12.2 系统设置

新增独立 Local Chrome 设置卡片，显示：

- 启用开关。
- Chrome 可执行文件路径。
- 只读 Profile 路径。
- 浏览器状态。
- “打开浏览器”按钮。
- “重启浏览器”按钮。

### 12.3 Watch 设置

Request 抓取方式中增加 `Local Chrome — Persistent Profile`。

功能未启用时不允许新选择。已有 Watch 使用该后端但功能不可用时，显示明确错误，不自动回退。

### 12.4 Watch 状态

新增用户可见状态：

- 等待本地浏览器。
- 正在使用本地浏览器。
- 需要浏览器操作。
- 本地 Chrome 不可用。

## 13. 错误处理

| 异常 | 处理 |
|---|---|
| 找不到 Chrome | 配置错误，不回退 |
| Playwright 未安装 | 显示依赖安装错误 |
| Chrome 被关闭 | 下次任务使用原 Profile 重启一次 |
| CDP 暂时不可用 | 等待启动并重连一次 |
| 检查中 Chrome 崩溃 | 无 Browser Steps 时最多重试一次；有 Browser Steps 时不自动重放 |
| Profile 被占用 | 报告占用，不结束未知进程 |
| Profile 无法读取 | 保留目录并报错，不删除、不重置 |
| 验证长期未处理 | 保持暂停，等待用户重试或取消 |
| 本地功能被禁用 | 使用该后端的 Watch 报配置错误 |

自动重试必须有明确上限，不能形成 Chrome 快速重启循环。

## 14. 安全要求

- CDP 仅监听 loopback，不能配置为远程地址。
- 使用动态端口，端口只作为运行时状态。
- Profile 目录依赖当前 Windows 用户权限保护。
- 不记录 Cookie、Authorization、表单密码或 Profile 文件内容。
- 不向远程 changedetection.io 实例暴露本地 CDP。
- 不自动执行凭据输入、验证码识别或反检测逻辑。
- 不允许配置日常 Chrome Profile 路径。
- 只结束经过 PID、可执行文件和命令行三重确认的自有进程。

## 15. 依赖

Windows 本地功能使用与项目 Dockerfile 一致的 Playwright Python 版本：

```text
playwright~=1.56.0
```

直接连接系统 Google Chrome，不下载 Playwright Chromium。

非 Windows 环境和未启用本功能的安装不应因为该功能启动 Chrome 或执行浏览器检测。

## 16. 测试策略

### 16.1 单元测试

覆盖：

- Chrome 路径发现和自定义路径校验。
- 启动参数使用专用 Profile 和 loopback CDP。
- `DevToolsActivePort` 解析。
- PID 所有权验证。
- 生命周期状态转换。
- 串行任务门。
- 人工处理状态的阻塞、重试和取消。
- 强弱登录检测信号。
- Profile 错误不会触发删除。
- 新后端不可用时不回退。

### 16.2 集成测试

使用临时 Profile 和本地测试页面验证：

1. 启动可见 Chrome。
2. 写入 Cookie 和 Local Storage。
3. 完成一次检查并关闭任务标签页。
4. 第二次检查读取相同状态。
5. Chrome 重启后状态仍存在。
6. 测试登录页面进入 `ATTENTION_REQUIRED`。
7. 模拟人工处理后继续提取。
8. 两个同时提交的 Watch 按顺序执行。

真实 Windows Chrome 测试单独标记，不要求普通 Linux CI 执行。

### 16.3 回归测试

确认：

- `html_requests` 行为不变。
- Selenium `html_webdriver` 行为不变。
- 远程 Playwright 行为不变。
- 老后端 Browser Steps 行为不变。
- Docker Compose 不启动 LocalChromeManager。
- 未启用本功能时不产生额外 Chrome 进程。

## 17. 验收标准

- Windows 直接运行 changedetection.io 后可以启用独立 Local Chrome。
- 系统使用专用持久 Profile 启动可见 Chrome。
- 用户登录一次后，后续检查复用登录状态。
- changedetection.io 和 Chrome 重启后登录状态仍存在。
- 本地浏览器任务严格串行。
- 正常任务只关闭自己的标签页。
- 登录或验证页面被检测后保留标签页并提示用户。
- 用户处理后可以继续检查或取消任务。
- 新功能不依赖 `WEBDRIVER_URL`、`PLAYWRIGHT_DRIVER_URL` 或 Extra Browsers。
- 新功能关闭时不启动 Chrome。
- 所有老 Fetcher 行为保持不变。
