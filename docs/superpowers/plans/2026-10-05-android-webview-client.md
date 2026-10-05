# Android WebView 信任设备客户端实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 构建一个可安装的 Android 8+ 客户端，通过原生扫码、Android Keystore 设备密钥和现有审批接口完成登录，再在受限 WebView 中打开帧藏网站。

**架构：** Android 原生层拥有二维码解析、私钥、配对状态、挑战签名和 Cookie；WebView 只消费认证会话并限制为已配对 origin。网页端增加 Android 专用二维码格式，后端仅允许来自私有网络的 Android 客户端通过 HTTP 领取一次性配对令牌，浏览器 HTTPS 规则保持不变。

**技术栈：** Kotlin、Android Views、Android Keystore、OkHttp 4.12、ZXing Android Embedded 4.3、AndroidX、AGP 9.3、Gradle 9.5、compile/target SDK 36、FastAPI、React/TypeScript、Vitest。

---

## 文件结构

### Android 构建与资源

- 创建 `android/settings.gradle.kts`：Gradle 仓库与 `app` 模块注册。
- 创建 `android/build.gradle.kts`：AGP 9.3 根插件配置。
- 创建 `android/gradle.properties`：AndroidX、JVM 与构建缓存配置。
- 创建 `android/gradle/wrapper/gradle-wrapper.properties`、`android/gradlew`、`android/gradlew.bat`、`android/gradle/wrapper/gradle-wrapper.jar`：Gradle 9.5 Wrapper。
- 创建 `android/app/build.gradle.kts`：SDK、依赖、测试、ViewBinding 与签名配置。
- 创建 `android/app/src/main/AndroidManifest.xml`：网络、相机与 Activity 声明。
- 创建 `android/app/src/main/res/xml/network_security_config.xml`：允许运行时严格校验后的局域网明文流量。
- 创建 `android/app/src/main/res/layout/activity_main.xml`：Activity 根容器。
- 创建 `android/app/src/main/res/layout/view_pairing.xml`：扫码/粘贴与错误状态。
- 创建 `android/app/src/main/res/layout/view_approval.xml`：验证码和等待状态。
- 创建 `android/app/src/main/res/layout/view_browser.xml`：WebView 与原生工具栏。
- 创建 `android/app/src/main/res/values/strings.xml`、`colors.xml`、`themes.xml`：中文文本和视觉主题。

### Android 领域与基础设施

- 创建 `android/app/src/main/java/com/framearchive/mobile/pairing/PairingLink.kt`：二维码强类型模型、解析和 origin 策略。
- 创建 `android/app/src/main/java/com/framearchive/mobile/crypto/DerSignatureConverter.kt`：DER ECDSA 转 `r || s`。
- 创建 `android/app/src/main/java/com/framearchive/mobile/crypto/DeviceKeyStore.kt`：Keystore P-256 生成、JWK 导出与签名。
- 创建 `android/app/src/main/java/com/framearchive/mobile/storage/DeviceIdentity.kt`：持久化身份与待审批模型。
- 创建 `android/app/src/main/java/com/framearchive/mobile/storage/EncryptedIdentityStore.kt`：AES-GCM 加密的 SharedPreferences 身份存储。
- 创建 `android/app/src/main/java/com/framearchive/mobile/network/DeviceAuthApi.kt`：配对、轮询、challenge、verify、状态读取和 Cookie 捕获。
- 创建 `android/app/src/main/java/com/framearchive/mobile/pairing/PairingRepository.kt`：配对和重新认证用例。
- 创建 `android/app/src/main/java/com/framearchive/mobile/ui/AppState.kt`：有限状态模型。
- 创建 `android/app/src/main/java/com/framearchive/mobile/ui/MainViewModel.kt`：状态恢复与轮询协调。
- 创建 `android/app/src/main/java/com/framearchive/mobile/web/TrustedOriginPolicy.kt`：WebView 同 origin 决策。
- 创建 `android/app/src/main/java/com/framearchive/mobile/web/WebSessionBridge.kt`：Cookie 注入、WebView 配置和 401 恢复。
- 创建 `android/app/src/main/java/com/framearchive/mobile/MainActivity.kt`：扫码、手工输入、状态渲染、WebView 与返回键。

### Android 测试

- 创建 `android/app/src/test/java/com/framearchive/mobile/pairing/PairingLinkTest.kt`。
- 创建 `android/app/src/test/java/com/framearchive/mobile/crypto/DerSignatureConverterTest.kt`。
- 创建 `android/app/src/test/java/com/framearchive/mobile/crypto/PublicJwkEncoderTest.kt`。
- 创建 `android/app/src/test/java/com/framearchive/mobile/network/DeviceAuthApiTest.kt`。
- 创建 `android/app/src/test/java/com/framearchive/mobile/storage/IdentityCodecTest.kt`。
- 创建 `android/app/src/test/java/com/framearchive/mobile/ui/MainViewModelTest.kt`。
- 创建 `android/app/src/test/java/com/framearchive/mobile/web/TrustedOriginPolicyTest.kt`。

### 现有项目改动

- 修改 `backend/app/api/device_auth.py`：`PairRequest.client_type` 与 Android HTTP 分支。
- 修改 `backend/app/services/device_auth.py`：私有客户端地址判断。
- 修改 `backend/tests/test_device_auth.py`：Android 私有 HTTP、浏览器 HTTP、公网 HTTP 回归测试。
- 创建 `frontend/src/lib/pairingPayload.ts`：浏览器/Android 二维码 payload 与 origin 校验。
- 创建 `frontend/src/lib/pairingPayload.test.ts`：二维码模式测试。
- 修改 `frontend/src/pages/TrustedDevicesPage.tsx`：二维码模式选择和 Android 地址提示。
- 修改 `frontend/package.json`、`frontend/pnpm-lock.yaml`：Vitest 命令与依赖。
- 修改 `.gitignore`：Android SDK、Gradle、签名、构建和 APK 产物。
- 修改 `README.md`：Android 构建、扫码、安装与安全说明。
- 创建 `scripts/bootstrap-android-sdk.sh`：项目本地 Android SDK 安装器。
- 创建 `scripts/build-android-apk.sh`：签名材料初始化、测试、Lint、APK 构建与校验值输出。

## 任务 1：搭建可重复的 Android 构建骨架

**文件：**
- 创建：`android/settings.gradle.kts`
- 创建：`android/build.gradle.kts`
- 创建：`android/gradle.properties`
- 创建：`android/app/build.gradle.kts`
- 创建：`android/app/src/main/AndroidManifest.xml`
- 创建：`android/app/src/main/res/layout/activity_main.xml`
- 创建：`android/app/src/main/res/values/strings.xml`
- 创建：`android/app/src/main/res/values/themes.xml`
- 创建：`scripts/bootstrap-android-sdk.sh`
- 修改：`.gitignore`

- [ ] **步骤 1：添加构建环境忽略规则**

在 `.gitignore` 中加入：

```gitignore
# Android 本地工具、签名与产物
/.android-sdk/
/android/.gradle/
/android/local.properties
/android/**/build/
/android/signing/
/artifacts/
```

- [ ] **步骤 2：创建 SDK 安装脚本**

`scripts/bootstrap-android-sdk.sh` 使用官方 Linux command-line tools `15859902`，先校验 SHA-256 `4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583`，再安装 `platform-tools`、`platforms;android-36` 与 `build-tools;36.0.0`。脚本不得自动吞掉许可证文本；首次运行在终端展示 `sdkmanager --licenses` 并等待明确接受。

核心变量必须固定为：

```bash
ANDROID_SDK_DIR="$PROJECT_ROOT/.android-sdk"
TOOLS_ARCHIVE="commandlinetools-linux-15859902_latest.zip"
TOOLS_SHA256="4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583"
```

- [ ] **步骤 3：创建 Gradle 配置**

根插件配置：

```kotlin
plugins {
    id("com.android.application") version "9.3.0" apply false
}
```

`android/app/build.gradle.kts` 至少包含：

```kotlin
plugins { id("com.android.application") }

android {
    namespace = "com.framearchive.mobile"
    compileSdk = 36
    defaultConfig {
        applicationId = "com.framearchive.mobile"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    buildFeatures { viewBinding = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.activity:activity-ktx:1.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.9.4")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
}
```

- [ ] **步骤 4：生成 Gradle Wrapper 并验证空工程**

运行：

```bash
cd android
gradle wrapper --gradle-version 9.5.0
ANDROID_HOME="$PWD/../.android-sdk" ./gradlew :app:assembleDebug
```

预期：`BUILD SUCCESSFUL`，生成 `android/app/build/outputs/apk/debug/app-debug.apk`。

- [ ] **步骤 5：Commit**

```bash
git add .gitignore android scripts/bootstrap-android-sdk.sh
git commit -m "build(Android): 初始化客户端工程"
```

## 任务 2：定义并测试 Android 二维码协议

**文件：**
- 创建：`frontend/src/lib/pairingPayload.ts`
- 创建：`frontend/src/lib/pairingPayload.test.ts`
- 修改：`frontend/package.json`
- 修改：`frontend/pnpm-lock.yaml`

- [ ] **步骤 1：添加 Vitest 并编写失败测试**

增加脚本 `"test": "vitest run"` 和开发依赖 `vitest`。测试必须覆盖：

```ts
expect(buildPairingPayload("android", "http://192.168.1.70:8420", "abc"))
  .toBe("framearchive://pair?v=1&server=http%3A%2F%2F192.168.1.70%3A8420&token=abc");
expect(() => buildPairingPayload("browser", "http://192.168.1.70:8420", "abc"))
  .toThrow("浏览器配对必须使用 HTTPS");
expect(() => buildPairingPayload("android", "http://8.8.8.8:8420", "abc"))
  .toThrow("Android HTTP 配对只允许私有局域网地址");
expect(buildPairingPayload("android", "https://video.example.com", "abc"))
  .toContain("framearchive://pair?");
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && pnpm test -- pairingPayload.test.ts`

预期：FAIL，提示 `buildPairingPayload` 不存在。

- [ ] **步骤 3：实现最小 payload 构建器**

导出：

```ts
export type PairingMode = "browser" | "android";
export function normalizePairingOrigin(value: string, mode: PairingMode): string;
export function buildPairingPayload(mode: PairingMode, origin: string, token: string): string;
```

校验 IPv4 `10/8`、`172.16/12`、`192.168/16`、回环，IPv6 `fc00::/7`、`fe80::/10` 与回环；拒绝凭据、非根路径、query、fragment 和非 HTTP(S) server origin。

- [ ] **步骤 4：运行测试和类型检查**

运行：

```bash
cd frontend
pnpm test -- pairingPayload.test.ts
pnpm build
```

预期：测试 PASS，TypeScript/Vite 构建成功。

- [ ] **步骤 5：Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml frontend/src/lib/pairingPayload.ts frontend/src/lib/pairingPayload.test.ts
git commit -m "test(设备信任): 定义 Android 配对二维码协议"
```

## 任务 3：在设置页生成 Android 专用二维码

**文件：**
- 修改：`frontend/src/pages/TrustedDevicesPage.tsx`
- 修改：`frontend/src/components/trusted-devices.css`

- [ ] **步骤 1：给 `PairingPanel` 增加模式状态**

模式控件必须明确显示：

```tsx
<label><input type="radio" checked={mode === "browser"} /> 浏览器</label>
<label><input type="radio" checked={mode === "android"} /> Android 应用</label>
```

Android 模式说明“允许私有局域网 HTTP；流量不会被加密”，浏览器模式继续显示 HTTPS 要求。

- [ ] **步骤 2：改用 `buildPairingPayload`**

现有 `pairTarget` 的 URL 拼接替换为：

```ts
const value = buildPairingPayload(mode, siteOrigin, pairing.token ?? "");
return { url: value, error: "" };
```

模式切换时清除“已复制”状态，但不重新领取配对令牌。

- [ ] **步骤 3：运行前端测试与构建**

运行：

```bash
cd frontend
pnpm test
pnpm build
```

预期：全部测试与构建通过，Android 模式对 `http://192.168.1.70:8420` 生成 `framearchive://pair` QR。

- [ ] **步骤 4：Commit**

```bash
git add frontend/src/pages/TrustedDevicesPage.tsx frontend/src/components/trusted-devices.css
git commit -m "feat(设备信任): 添加 Android 应用二维码模式"
```

## 任务 4：后端仅为私有网络 Android 客户端放行 HTTP 配对

**文件：**
- 修改：`backend/app/services/device_auth.py`
- 修改：`backend/app/api/device_auth.py`
- 修改：`backend/tests/test_device_auth.py`

- [ ] **步骤 1：编写失败的 API 测试**

创建三个不同 ASGI 客户端并分别领取新的配对令牌：

```python
private_http = httpx.AsyncClient(
    transport=httpx.ASGITransport(app=app, client=("192.168.1.20", 1234)),
    base_url="http://test",
)
public_http = httpx.AsyncClient(
    transport=httpx.ASGITransport(app=app, client=("8.8.8.8", 1234)),
    base_url="http://test",
)

assert browser_http.status_code == 400
assert android_private_http.status_code == 202
assert android_public_http.status_code == 400
```

Android 请求 JSON 显式包含 `"client_type": "android"`；浏览器请求省略该字段。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && .venv/bin/pytest -q tests/test_device_auth.py`

预期：Android 私有 HTTP 请求返回 400，而测试期待 202。

- [ ] **步骤 3：实现私有客户端判断和请求类型**

在服务层增加：

```python
def is_private_client(request: Request) -> bool:
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local
```

`PairRequest` 增加：

```python
client_type: Literal["browser", "android"] = "browser"
```

配对条件改为：HTTPS/loopback 保持允许；仅当 `client_type == "android"` 且直接客户端地址为私有、回环或链路本地时允许 HTTP。不得信任客户端提供的 `X-Forwarded-For`。

- [ ] **步骤 4：运行专项测试与 Ruff**

运行：

```bash
cd backend
.venv/bin/pytest -q tests/test_device_auth.py
.venv/bin/ruff check app/api/device_auth.py app/services/device_auth.py tests/test_device_auth.py
```

预期：设备认证测试全部 PASS，Ruff 无问题。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/api/device_auth.py backend/app/services/device_auth.py backend/tests/test_device_auth.py
git commit -m "feat(设备信任): 允许 Android 私网 HTTP 配对"
```

## 任务 5：实现 Android 二维码解析与 origin 策略

**文件：**
- 创建：`android/app/src/main/java/com/framearchive/mobile/pairing/PairingLink.kt`
- 创建：`android/app/src/test/java/com/framearchive/mobile/pairing/PairingLinkTest.kt`

- [ ] **步骤 1：编写失败的解析测试**

测试至少包含：

```kotlin
@Test fun parsesPrivateLanPairingLink() {
    val link = PairingLink.parse(
        "framearchive://pair?v=1&server=http%3A%2F%2F192.168.1.70%3A8420&token=abc"
    )
    assertEquals("http://192.168.1.70:8420", link.serverOrigin)
    assertEquals("abc", link.token)
}

@Test fun rejectsPublicCleartextServer() {
    assertThrows(InvalidPairingLink::class.java) {
        PairingLink.parse("framearchive://pair?v=1&server=http%3A%2F%2F8.8.8.8&token=abc")
    }
}
```

另测未知版本、空令牌、凭据、路径、query/fragment、IPv6 私网、HTTPS 域名。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd android && ANDROID_HOME="$PWD/../.android-sdk" ./gradlew testDebugUnitTest --tests '*PairingLinkTest'`

预期：FAIL，类不存在。

- [ ] **步骤 3：实现不可变模型与解析器**

公开接口固定为：

```kotlin
data class PairingLink(val serverOrigin: String, val token: String) {
    companion object { fun parse(raw: String): PairingLink }
}
class InvalidPairingLink(message: String) : IllegalArgumentException(message)
```

地址策略集中在同文件的内部 `TrustedServerOrigin`，网络和 WebView 模块复用 `PairingLink.serverOrigin`，禁止各自重新解析宽松地址。

- [ ] **步骤 4：运行测试验证通过**

运行同上，预期 `PairingLinkTest` PASS。

- [ ] **步骤 5：Commit**

```bash
git add android/app/src/main/java/com/framearchive/mobile/pairing android/app/src/test/java/com/framearchive/mobile/pairing
git commit -m "feat(Android): 解析安全的设备配对二维码"
```

## 任务 6：实现 P-256 Keystore 密钥与签名格式

**文件：**
- 创建：`android/app/src/main/java/com/framearchive/mobile/crypto/DerSignatureConverter.kt`
- 创建：`android/app/src/main/java/com/framearchive/mobile/crypto/DeviceKeyStore.kt`
- 创建：`android/app/src/test/java/com/framearchive/mobile/crypto/DerSignatureConverterTest.kt`
- 创建：`android/app/src/test/java/com/framearchive/mobile/crypto/PublicJwkEncoderTest.kt`

- [ ] **步骤 1：编写 DER 边界测试**

构造包含正数前导 `00`、31/32/33 字节整数和非法长度的 DER。核心断言：

```kotlin
val raw = DerSignatureConverter.toP1363(der)
assertEquals(64, raw.size)
assertArrayEquals(expectedR, raw.copyOfRange(0, 32))
assertArrayEquals(expectedS, raw.copyOfRange(32, 64))
```

- [ ] **步骤 2：编写 JWK 坐标测试**

把已知 `ECPublicKey` 坐标编码为无填充 base64url，断言 `x`、`y` 解码后始终各 32 字节，即使大整数原始表示带符号前导字节。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd android && ANDROID_HOME="$PWD/../.android-sdk" ./gradlew testDebugUnitTest --tests '*crypto*'`

预期：FAIL，转换器和编码器不存在。

- [ ] **步骤 4：实现转换器与 Keystore**

`DeviceKeyStore` 接口：

```kotlin
data class PublicJwk(val kty: String = "EC", val crv: String = "P-256", val x: String, val y: String)

class DeviceKeyStore {
    fun create(alias: String): PublicJwk
    fun publicJwk(alias: String): PublicJwk
    fun sign(alias: String, challenge: ByteArray): ByteArray
    fun delete(alias: String)
    fun contains(alias: String): Boolean
}
```

KeyGenParameterSpec 使用 `PURPOSE_SIGN or PURPOSE_VERIFY`、`ECGenParameterSpec("secp256r1")`、`DIGEST_SHA256`；`sign` 返回经 `DerSignatureConverter` 处理的 64 字节格式。

- [ ] **步骤 5：运行加密测试与 Android Lint**

运行：

```bash
cd android
ANDROID_HOME="$PWD/../.android-sdk" ./gradlew testDebugUnitTest --tests '*crypto*' lintDebug
```

预期：测试 PASS，Lint 无 error。

- [ ] **步骤 6：Commit**

```bash
git add android/app/src/main/java/com/framearchive/mobile/crypto android/app/src/test/java/com/framearchive/mobile/crypto
git commit -m "feat(Android): 添加不可导出设备密钥"
```

## 任务 7：实现加密身份存储

**文件：**
- 创建：`android/app/src/main/java/com/framearchive/mobile/storage/DeviceIdentity.kt`
- 创建：`android/app/src/main/java/com/framearchive/mobile/storage/EncryptedIdentityStore.kt`
- 创建：`android/app/src/test/java/com/framearchive/mobile/storage/IdentityCodecTest.kt`

- [ ] **步骤 1：编写身份序列化失败测试**

定义并往返以下模型：

```kotlin
data class DeviceIdentity(
    val serverOrigin: String,
    val deviceId: Long,
    val deviceName: String,
    val keyAlias: String,
    val pending: PendingPairing? = null,
)

data class PendingPairing(
    val pairingId: Long,
    val claimSecret: String,
    val verificationCode: String,
    val expiresAtEpochMillis: Long,
)
```

测试批准后 `copy(pending = null)` 不再编码 claim secret，损坏/未知版本数据返回可识别错误而不是部分身份。

- [ ] **步骤 2：实现版本化 JSON codec**

存储 JSON 顶层固定包含 `version: 1` 和 `identity`。使用 `org.json`，不引入反射序列化框架。

- [ ] **步骤 3：实现 AES-GCM 存储**

`EncryptedIdentityStore` 在 Android Keystore 创建 AES-256 GCM key `frame_archive_identity_store_v1`，SharedPreferences 只保存随机 12 字节 IV 与密文。接口：

```kotlin
interface IdentityStore {
    fun read(): DeviceIdentity?
    fun save(identity: DeviceIdentity)
    fun clear()
}
```

认证失败或 key 永久失效时删除不可解密密文并返回明确的 `IdentityStoreCorrupted`。

- [ ] **步骤 4：运行测试**

运行：`cd android && ANDROID_HOME="$PWD/../.android-sdk" ./gradlew testDebugUnitTest --tests '*IdentityCodecTest'`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add android/app/src/main/java/com/framearchive/mobile/storage android/app/src/test/java/com/framearchive/mobile/storage
git commit -m "feat(Android): 加密保存设备身份"
```

## 任务 8：实现设备认证 API 与配对仓库

**文件：**
- 创建：`android/app/src/main/java/com/framearchive/mobile/network/DeviceAuthApi.kt`
- 创建：`android/app/src/main/java/com/framearchive/mobile/pairing/PairingRepository.kt`
- 创建：`android/app/src/test/java/com/framearchive/mobile/network/DeviceAuthApiTest.kt`

- [ ] **步骤 1：用 MockWebServer 编写失败测试**

覆盖：

```kotlin
@Test fun pairSendsAndroidClientType()
@Test fun verifyCapturesOnlyFrameDeviceSessionCookie()
@Test fun redirectToDifferentOriginIsRejected()
@Test fun apiErrorUsesFastApiDetailMessage()
@Test fun approvedPairingAuthenticatesWithRawSignature()
```

请求体必须断言包含 `"client_type":"android"`、设备名和 P-256 JWK；verify 响应同时包含无关 Cookie 时只保留 `frame_device_session`。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd android && ANDROID_HOME="$PWD/../.android-sdk" ./gradlew testDebugUnitTest --tests '*DeviceAuthApiTest'`

预期：FAIL，API 类不存在。

- [ ] **步骤 3：实现 API DTO 与同 origin OkHttpClient**

固定接口：

```kotlin
data class PairClaim(val pairingId: Long, val deviceId: Long, val claimSecret: String, val verificationCode: String)
sealed interface PairingStatus { data object Pending; data object Approved; data object Rejected; data object Expired }
data class AuthSession(val setCookieHeader: String)

class DeviceAuthApi(private val serverOrigin: String, client: OkHttpClient) {
    suspend fun requestPairing(token: String, name: String, jwk: PublicJwk): PairClaim
    suspend fun pairingStatus(pairingId: Long, claimSecret: String): PairingStatus
    suspend fun challenge(deviceId: Long): String
    suspend fun verify(deviceId: Long, challenge: String, signature: ByteArray): AuthSession
    suspend fun isAuthenticated(): Boolean
}
```

所有调用设置连接/读取超时；关闭跨 origin redirect；错误映射为 `PairingExpired`、`PairingRejected`、`DeviceRevoked`、`NetworkUnavailable`。

- [ ] **步骤 4：实现 `PairingRepository`**

仓库负责生成 alias、创建密钥、提交申请、保存 pending、轮询、批准后清除 pending 并挑战登录。轮询间隔 1500ms，到二维码 `expiresAt` 后停止。失败创建必须删除刚生成但未注册成功的 key alias。

- [ ] **步骤 5：运行 API 测试**

运行同步骤 2，预期 PASS。

- [ ] **步骤 6：Commit**

```bash
git add android/app/src/main/java/com/framearchive/mobile/network android/app/src/main/java/com/framearchive/mobile/pairing/PairingRepository.kt android/app/src/test/java/com/framearchive/mobile/network
git commit -m "feat(Android): 接入设备配对与签名认证"
```

## 任务 9：实现可恢复 UI、扫码和手工输入

**文件：**
- 创建：`android/app/src/main/java/com/framearchive/mobile/ui/AppState.kt`
- 创建：`android/app/src/main/java/com/framearchive/mobile/ui/MainViewModel.kt`
- 创建：`android/app/src/main/java/com/framearchive/mobile/MainActivity.kt`
- 创建：`android/app/src/main/res/layout/view_pairing.xml`
- 创建：`android/app/src/main/res/layout/view_approval.xml`
- 修改：`android/app/src/main/res/layout/activity_main.xml`
- 修改：`android/app/src/main/res/values/strings.xml`
- 创建：`android/app/src/test/java/com/framearchive/mobile/ui/MainViewModelTest.kt`

- [ ] **步骤 1：编写 ViewModel 状态测试**

使用 fake repository 和 coroutine test scheduler，覆盖：

```kotlin
@Test fun restoresPendingPairingAfterRestart()
@Test fun approvedPairingTransitionsToAuthenticatingThenBrowsing()
@Test fun rejectedPairingClearsIdentityAndReturnsToScanner()
@Test fun networkFailureKeepsRetryablePendingIdentity()
@Test fun revokedIdentityReturnsToScanner()
```

- [ ] **步骤 2：定义有限状态并验证测试失败**

`AppState` 只允许：

```kotlin
sealed interface AppState {
    data object NeedsPairing : AppState
    data object Submitting : AppState
    data class AwaitingApproval(val code: String, val expiresAt: Long) : AppState
    data object Authenticating : AppState
    data class Browsing(val serverOrigin: String, val cookieHeader: String) : AppState
    data class RecoverableError(val message: String, val canRetry: Boolean) : AppState
}
```

运行 ViewModel 测试，预期因协调逻辑缺失而 FAIL。

- [ ] **步骤 3：实现 ViewModel 恢复和轮询**

初始化顺序：pending → 恢复轮询；已批准 identity → challenge 登录；无 identity → NeedsPairing。`onCleared` 取消轮询，但不清除持久化 pending。

- [ ] **步骤 4：实现扫码与粘贴 UI**

使用 `ScanContract`/`ScanOptions` 启动 ZXing；只接受 QR code。相机权限失败或用户取消时保留“粘贴配对链接”输入框。输入在提交前统一调用 `PairingLink.parse`。

- [ ] **步骤 5：实现验证码与错误 UI**

验证码显示为 `123 456`，同时显示服务器 host、设备名、剩余有效期和取消按钮。取消必须删除未批准身份及 key alias。

- [ ] **步骤 6：运行 ViewModel 测试和 Lint**

运行：

```bash
cd android
ANDROID_HOME="$PWD/../.android-sdk" ./gradlew testDebugUnitTest --tests '*MainViewModelTest' lintDebug
```

预期：PASS，无 Lint error。

- [ ] **步骤 7：Commit**

```bash
git add android/app/src/main/java/com/framearchive/mobile/ui android/app/src/main/java/com/framearchive/mobile/MainActivity.kt android/app/src/main/res android/app/src/test/java/com/framearchive/mobile/ui
git commit -m "feat(Android): 添加扫码与审批恢复界面"
```

## 任务 10：桥接 Cookie 并限制 WebView

**文件：**
- 创建：`android/app/src/main/java/com/framearchive/mobile/web/TrustedOriginPolicy.kt`
- 创建：`android/app/src/main/java/com/framearchive/mobile/web/WebSessionBridge.kt`
- 创建：`android/app/src/test/java/com/framearchive/mobile/web/TrustedOriginPolicyTest.kt`
- 创建：`android/app/src/main/res/layout/view_browser.xml`
- 创建：`android/app/src/main/res/xml/network_security_config.xml`
- 修改：`android/app/src/main/AndroidManifest.xml`
- 修改：`android/app/src/main/java/com/framearchive/mobile/MainActivity.kt`

- [ ] **步骤 1：编写 origin 决策测试**

```kotlin
assertEquals(SAME_ORIGIN, policy.decide("http://192.168.1.70:8420/videos/1"))
assertEquals(EXTERNAL_HTTPS, policy.decide("https://example.com/help"))
assertEquals(BLOCK, policy.decide("http://example.com/steal"))
assertEquals(BLOCK, policy.decide("http://192.168.1.71:8420/"))
```

同时测试伪装 host、不同 port、`intent:`、`file:`、`content:` 与 user-info URL。

- [ ] **步骤 2：实现 `TrustedOriginPolicy` 并通过测试**

只比较规范化 scheme、ASCII host 与有效 port；不使用字符串前缀判断。

- [ ] **步骤 3：实现 Cookie 桥接**

`WebSessionBridge.open(webView, serverOrigin, setCookieHeader)` 顺序固定为：清除该 origin 旧 session、`setAcceptCookie(true)`、`setCookie`、`flush`、配置 WebView、加载 `${serverOrigin}/`。禁止 `addJavascriptInterface`。

- [ ] **步骤 4：实现安全 WebViewClient**

同 origin 在 WebView 内打开；外部 HTTPS 发 `ACTION_VIEW`；其他请求显示阻止提示。`onReceivedSslError` 必须 `handler.cancel()`，不得绕过证书错误。下载交给系统 `DownloadManager` 前再次检查同 origin。

- [ ] **步骤 5：处理返回键和恢复认证**

返回键先 `webView.goBack()`；Activity `onResume` 调用轻量设备认证刷新 Cookie。网页显示未授权时，用户点原生“重新认证”按钮回到 challenge 流程。

- [ ] **步骤 6：运行 Web 测试、全部 Android 单测和 Lint**

运行：

```bash
cd android
ANDROID_HOME="$PWD/../.android-sdk" ./gradlew testDebugUnitTest lintDebug
```

预期：全部 PASS，无 Lint error。

- [ ] **步骤 7：Commit**

```bash
git add android/app/src/main/java/com/framearchive/mobile/web android/app/src/test/java/com/framearchive/mobile/web android/app/src/main/res/layout/view_browser.xml android/app/src/main/res/xml android/app/src/main/AndroidManifest.xml android/app/src/main/java/com/framearchive/mobile/MainActivity.kt
git commit -m "feat(Android): 在受限 WebView 中打开帧藏"
```

## 任务 11：生成稳定签名 APK 与使用文档

**文件：**
- 创建：`scripts/build-android-apk.sh`
- 修改：`android/app/build.gradle.kts`
- 修改：`README.md`

- [ ] **步骤 1：实现本地签名初始化**

脚本在 `android/signing/` 创建一次性持久保存的 release keystore 和 `keystore.properties`。随机密码分别生成并写入权限 `0600` 的忽略文件；已有 keystore 时不得覆盖。`build.gradle.kts` 只在该 properties 存在时启用 release signing。

固定 alias：`frame-archive-mobile`；证书主题：`CN=Frame Archive Mobile, O=Local`；有效期 25 年。脚本结尾提醒用户备份整个 `android/signing/`，否则以后 APK 无法覆盖安装。

- [ ] **步骤 2：实现一键验证与产物复制**

脚本执行：

```bash
./gradlew testReleaseUnitTest lintRelease assembleRelease
cp app/build/outputs/apk/release/app-release.apk ../artifacts/frame-archive-mobile-0.1.0.apk
sha256sum ../artifacts/frame-archive-mobile-0.1.0.apk > ../artifacts/frame-archive-mobile-0.1.0.apk.sha256
```

脚本显式设置项目 `.android-sdk` 为 `ANDROID_HOME`，任一验证失败立即退出。

- [ ] **步骤 3：更新 README**

记录：

- 电脑设置页选择 Android 模式并填写 `http://192.168.1.70:8420`。
- 手机上允许“安装未知应用”的系统步骤、APK 文件与 SHA-256 验证方法。
- 扫码、验证码批准、撤销设备和重新扫码流程。
- 私有 HTTP 的明文风险及 HTTPS 推荐。
- 备份 `android/signing/` 的升级必要性。
- 2026 年直接侧载仍可用；若设备启用 Android 的高级未验证开发者限制，安装步骤由用户在系统设置中明确完成，程序不得尝试绕过。

- [ ] **步骤 4：构建 release APK**

运行：`./scripts/build-android-apk.sh`

预期：生成：

```text
artifacts/frame-archive-mobile-0.1.0.apk
artifacts/frame-archive-mobile-0.1.0.apk.sha256
```

- [ ] **步骤 5：Commit**

```bash
git add android/app/build.gradle.kts scripts/build-android-apk.sh README.md
git commit -m "build(Android): 生成可安装客户端 APK"
```

## 任务 12：端到端回归、实机边界与交付审查

**文件：**
- 修改（仅发现问题时）：上述实现文件与对应测试。

- [ ] **步骤 1：运行全量后端测试**

运行：`cd backend && .venv/bin/pytest -q`

预期：全部 PASS；允许仓库既有、原因明确的 skip，不允许新增失败。

- [ ] **步骤 2：运行前端测试与生产构建**

运行：

```bash
cd frontend
pnpm test
pnpm build
```

预期：全部 PASS，构建成功。

- [ ] **步骤 3：运行 Android 全量检查**

运行：

```bash
cd android
ANDROID_HOME="$PWD/../.android-sdk" ./gradlew testDebugUnitTest testReleaseUnitTest lintDebug lintRelease assembleDebug assembleRelease
```

预期：全部 PASS，debug/release APK 均生成。

- [ ] **步骤 4：执行协议级端到端验收**

使用真实后端创建 Android QR，验证：Android 私网 HTTP 请求可创建 pending；相同 token 重放返回 409；未批准访问返回 401；批准后 challenge 签名得到 Cookie；撤销后 Cookie 返回 401。无实机时用 Android 单测 + 后端集成测试完成协议验收，并在交付中把相机/WebView 实机验收标为未执行。

- [ ] **步骤 5：有设备时执行实机验收**

通过 `adb install -r artifacts/frame-archive-mobile-0.1.0.apk` 安装，逐项验证扫码、验证码、批准、WebView、旋转/重启恢复和撤销。如果 `adb devices` 没有授权设备，停止此步骤并如实记录，不得宣称实机通过。

- [ ] **步骤 6：使用 code-review 技能审查固定差异**

固定点使用实现前的设计提交 `9fdb416`，审查 `git diff 9fdb416...HEAD` 的标准与规格符合性。修复全部 P1/P2 和安全边界问题后重新运行相关测试。

- [ ] **步骤 7：最终状态检查与交付提交**

运行：

```bash
git status --short
git log --oneline 9fdb416..HEAD
sha256sum artifacts/frame-archive-mobile-0.1.0.apk
```

工作树应为空；若审查修复尚未提交，使用：

```bash
git add -u
git commit -m "fix(Android): 完成客户端交付审查"
```

最终交付 APK 绝对路径、SHA-256、最低 Android 版本、安装步骤、测试统计、实机验收状态和签名备份位置。
