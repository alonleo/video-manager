export type PairingMode = "browser" | "android";

function isPrivateIpv4(hostname: string): boolean {
  if (!/^\d+\.\d+\.\d+\.\d+$/.test(hostname)) {
    return false;
  }

  const [first, second] = hostname.split(".").map(Number);
  return (
    first === 10 ||
    first === 127 ||
    (first === 172 && second >= 16 && second <= 31) ||
    (first === 192 && second === 168)
  );
}

function isPrivateIpv6(hostname: string): boolean {
  const address = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (address === "::1") {
    return true;
  }

  const firstHextet = Number.parseInt(address.split(":", 1)[0], 16);
  return (
    (firstHextet >= 0xfc00 && firstHextet <= 0xfdff) ||
    (firstHextet >= 0xfe80 && firstHextet <= 0xfebf)
  );
}

function isPrivateHost(hostname: string): boolean {
  if (hostname.toLowerCase() === "localhost") {
    return true;
  }
  return hostname.includes(":")
    ? isPrivateIpv6(hostname)
    : isPrivateIpv4(hostname);
}

export function normalizePairingOrigin(value: string, mode: PairingMode): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("配对服务器地址无效");
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("配对服务器地址必须使用 HTTP 或 HTTPS");
  }
  if (url.username || url.password || /^[a-z][a-z\d+.-]*:\/\/[^/?#]*@/i.test(value.trim())) {
    throw new Error("配对服务器地址不能包含凭据");
  }
  if (url.pathname !== "/") {
    throw new Error("配对服务器地址只能使用根路径");
  }
  if (value.includes("?")) {
    throw new Error("配对服务器地址不能包含查询参数");
  }
  if (value.includes("#")) {
    throw new Error("配对服务器地址不能包含片段");
  }
  if (mode === "browser" && url.protocol !== "https:") {
    throw new Error("浏览器配对必须使用 HTTPS");
  }
  if (mode === "android" && url.protocol === "http:" && !isPrivateHost(url.hostname)) {
    throw new Error("Android HTTP 配对只允许私有局域网地址");
  }

  return url.origin;
}

export function buildPairingPayload(
  mode: PairingMode,
  origin: string,
  token: string,
): string {
  const normalizedOrigin = normalizePairingOrigin(origin, mode);
  if (mode === "browser") {
    return `${normalizedOrigin}/pair#token=${encodeURIComponent(token)}`;
  }
  return `framearchive://pair?v=1&server=${encodeURIComponent(normalizedOrigin)}&token=${encodeURIComponent(token)}`;
}
