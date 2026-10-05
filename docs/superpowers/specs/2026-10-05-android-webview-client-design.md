# Android WebView 信任设备客户端设计

日期：2026-10-05
状态：已批准

## 目标

为帧藏提供一个可直接安装的 Android 客户端。用户在电脑的“信任设备”页面生成二维码，手机应用扫码后生成独立设备密钥，等待电脑核对六位验证码并批准，随后在受限 WebView 中使用完整网站。

该客户端用于解决移动浏览器访问私有局域网 HTTP 地址时无法使用 Web Crypto、因而不能完成现有网页配对的问题。

## 第一版范围

包含：

- Android 8.0（API 26）及以上。
- 原生相机扫码，以及手动粘贴配对链接作为权限拒绝时的后备入口。
- Android Keystore P-256 设备密钥，私钥不可导出。
- 提交配对申请、显示六位验证码、轮询批准结果。
- 挑战签名登录、会话 Cookie 桥接和受限 WebView。
- 未完成配对与设备身份的进程重启恢复。
- 设备撤销、密钥失效、二维码过期、拒绝和网络失败的恢复界面。
- 可直接安装的 APK、构建说明和安装说明。

不包含：

- 重新实现原生视频库、播放器或照片库。
- 推送通知、后台常驻或后台自动重连。
- 存储源、AI、下载、字幕等功能的原生界面。
- 公网 HTTP 服务器访问。
- 第一版内自动部署 HTTPS。

## 用户流程

1. 已信任电脑打开“设置 → 信任设备”，选择“Android 应用”二维码模式。
2. 电脑填写手机实际可达的服务器地址，例如 `http://192.168.1.70:8420`。
3. 设置页验证该地址是 HTTPS，或是私有/回环/链路本地地址上的 HTTP，然后生成专用二维码。
4. 手机应用扫描二维码，验证协议、服务器地址和令牌格式。
5. 应用在 Android Keystore 生成不可导出的 P-256 密钥，并向服务器提交设备名称和 JWK 公钥。
6. 手机与电脑显示相同六位验证码；用户在电脑批准。
7. 应用轮询到批准结果，执行挑战签名登录，取得会话 Cookie。
8. 应用将 Cookie 写入 WebView `CookieManager`，只加载二维码指定的服务器 origin。
9. 后续启动先用设备私钥刷新会话，再进入 WebView。设备被撤销后返回扫码首页。

## 二维码协议

Android 模式使用专用 URI，避免相机或普通浏览器误把配对链接当网页打开：

```text
framearchive://pair?v=1&server=<percent-encoded-origin>&token=<base64url-token>
```

约束：

- `v` 第一版固定为 `1`，未知版本拒绝并提示升级应用。
- `server` 必须是仅含 scheme、host 和显式/默认 port 的 origin，不允许用户名、密码、查询、片段和非根路径。
- HTTPS 地址允许正常域名或 IP。
- HTTP 地址只允许 IPv4 私有地址、IPv6 唯一本地/链路本地地址、`localhost` 或回环地址。
- 禁止重定向到不同 origin；网络层与 WebView 均执行同一校验。
- `token` 沿用现有 32 字节随机、两分钟有效、一次性令牌。
- 普通浏览器二维码保持现有 HTTPS 规则，不因 Android 模式放宽。

## Android 架构

项目位于 `android/`，使用 Kotlin、Android Views、Gradle Wrapper 和单 Activity 状态界面。

### `PairingContract`

定义二维码版本、字段和服务器 origin 校验。`QrPairingParser` 将原始扫码内容解析为强类型 `PairingLink`，其他模块不直接操作 URI 字符串。

### `DeviceKeyStore`

在 Android Keystore 中按设备身份创建 `secp256r1`/P-256 ECDSA 密钥。只导出公钥并编码为后端现有的 JWK `x`、`y` 字段。签名使用 SHA-256 ECDSA；Android 返回的 DER 签名转换为固定 64 字节 `r || s`，再做 base64url 编码。

### `PairingRepository`

通过原生 HTTP 客户端调用现有接口：

- `POST /api/device-auth/pair`
- `POST /api/device-auth/pair-status`
- `POST /api/device-auth/challenge`
- `POST /api/device-auth/verify`
- `GET /api/device-auth/status`

它负责超时、同 origin 重定向策略、响应错误映射和会话 Cookie 捕获。请求不依赖 WebView 或网页 JavaScript。

### `IdentityStore`

持久化非秘密元数据：服务器 origin、设备 ID、Keystore alias、设备名称，以及等待审批期间的 pairing ID、claim secret、验证码和过期时间。claim secret 使用 Android 加密存储；终态后立即清除。私钥始终只存在 Keystore。

### `SessionAuthenticator`

读取身份、请求 challenge、用 Keystore 私钥签名并调用 verify。认证成功后把服务端 `frame_device_session` Cookie 交给 `WebSessionBridge`。签名失败、设备不存在或已撤销时删除失效身份并返回扫码状态。

### `WebSessionBridge`

使用 Android `CookieManager` 写入与服务器 origin 匹配的会话 Cookie，再加载网站。WebView 启用站点运行所需的 JavaScript、DOM storage 和媒体能力，但不提供设备私钥桥接对象。

导航限制：

- 主框架只允许已配对服务器的同 origin URL。
- 不同 origin 的 HTTPS 链接交给系统浏览器。
- 不同 origin 的 HTTP 链接拒绝。
- 网页下载由应用内受控下载器处理，不交给系统 `DownloadManager`。HTTP 客户端关闭自动重定向，
  每一跳都先验证为已配对的同 origin（最多五跳）再携带会话 Cookie 发起下一请求；文件名经过清洗，
  临时文件与完成文件只写入应用专用的 Downloads 目录，避免跨 origin 重定向泄露认证 Cookie。
- 文件选择、相机、麦克风、定位和任意证书错误默认拒绝。
- Android 返回键优先返回 WebView 历史，无法返回时退出 Activity。

### `MainActivity` 状态

- `NeedsPairing`：扫码或粘贴链接。
- `Submitting`：创建密钥并提交申请。
- `AwaitingApproval`：显示验证码并轮询，可在进程重启后恢复。
- `Authenticating`：挑战签名与 Cookie 桥接。
- `Browsing`：展示 WebView。
- `RecoverableError`：网络、过期、拒绝或格式错误，保留安全的重试路径。

Activity/ViewModel 只协调状态；二维码、密钥、网络、存储和 WebView 逻辑由上述组件隔离。

## Web 与后端改动

设备设置页增加配对模式：

- “浏览器”：保持当前网页 URL 二维码，远程地址必须 HTTPS。
- “Android 应用”：生成 `framearchive://pair` URI，允许经过校验的私有局域网 HTTP origin。

后端沿用现有配对和认证 API、P-256 JWK、一次性令牌、六位验证码、审批及撤销语义。第一版不新增 Android 专用认证后门，也不降低浏览器端安全检查。

如原生客户端需要读取 HttpOnly Cookie，网络客户端从 `Set-Cookie` 响应头按 Cookie 规范解析；后端不在 JSON 中返回会话令牌。

## 安全边界

Android 原生 Keystore 解决的是“哪些设备可以登录”，不是 HTTP 传输加密。私有局域网 HTTP 模式存在以下已接受风险：

- 同一网络中的攻击者可能观察页面、媒体地址和会话流量。
- 会话 Cookie 在明文链路上可能被窃取。
- 因此应用只允许二维码明确指定的私有地址，不允许公网 HTTP，也不宣称提供传输保密。

设备准入仍由一次性随机令牌、人工核对验证码、电脑端批准、设备私钥挑战和可撤销服务端会话共同保证。后续版本可增加证书绑定 HTTPS，而不改变设备身份模型。

## 错误处理

- 相机权限拒绝：显示权限说明和手动粘贴入口，不循环请求权限。
- 非法二维码：不发网络请求，指出格式或地址错误。
- 服务器不可达：保留未过期申请和身份，提供重试及重新扫码。
- 二维码已使用或过期：删除本次未批准身份与密钥，返回扫码页。
- 申请被拒绝：删除本次身份与密钥，明确提示由电脑拒绝。
- 应用在等待期间重启：从 `IdentityStore` 恢复轮询和验证码。
- 已批准但登录中断：下次启动使用保存的设备身份重新执行 challenge。
- 设备撤销或 Keystore 密钥丢失：清除身份、Cookie 和 WebView 数据，要求重新扫码。
- WebView 主页面出现 401：回到原生认证流程；签名仍有效时自动刷新会话。

## 构建与交付

- 最低 SDK 26；目标/编译 SDK 采用构建环境可用的稳定版本。
- 仓库提交 Gradle Wrapper 和全部源码，不提交 Android SDK、Gradle 缓存、APK 中间产物、签名密码或私钥。
- 在工作区安装项目专用 Android SDK，不修改系统级 Android Studio 配置。
- 生成可直接侧载的 APK。第一版使用固定的本地签名材料，以便后续构建覆盖安装；签名材料保存在 Git 忽略路径，并在交付时说明备份位置。
- APK 输出复制到稳定的项目产物目录，并提供 SHA-256 校验值。

## 测试与验收

JVM 单元测试：

- Android/浏览器二维码的合法与非法输入。
- 私有 IPv4、IPv6、回环、公网 HTTP 和 HTTPS origin 校验。
- JWK 坐标固定长度及前导零处理。
- DER ECDSA 签名到 64 字节格式的边界值。
- 配对状态持久化、终态清理和进程恢复。
- Cookie 解析及同 origin 限制。

后端/前端回归：

- Android 二维码模式仅放宽私有 HTTP 地址。
- 浏览器模式仍拒绝远程 HTTP。
- 一次性令牌不可重放，未批准设备不能访问，撤销立即生效。
- 现有网页设备配对测试继续通过。

构建验证：

- Android unit tests、Lint 和 assemble APK。
- 后端完整测试套件。
- 前端 TypeScript 生产构建。
- 在 Android 8+ 实机或可用模拟器完成扫码、批准、重启恢复、WebView 登录和撤销验收；没有可用设备/模拟器时明确标记实机验收未执行，不能用编译成功替代。

验收标准：用户无需在手机安装 CA 证书，能扫描电脑显示的 Android 二维码、核对并等待批准，随后在应用内打开 `192.168.x.x` 上的帧藏网站；应用重启后仍可登录，被撤销后不能继续访问。
