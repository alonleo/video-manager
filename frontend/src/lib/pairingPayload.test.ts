import { describe, expect, it } from "vitest";

import {
  buildPairingPayload,
  normalizePairingOrigin,
  type PairingMode,
} from "./pairingPayload";

describe("buildPairingPayload", () => {
  it("builds the documented Android LAN payload", () => {
    expect(
      buildPairingPayload("android", "http://192.168.1.70:8420", "abc"),
    ).toBe(
      "framearchive://pair?v=1&server=http%3A%2F%2F192.168.1.70%3A8420&token=abc",
    );
  });

  it("requires HTTPS in browser mode", () => {
    expect(() =>
      buildPairingPayload("browser", "http://192.168.1.70:8420", "abc"),
    ).toThrow("浏览器配对必须使用 HTTPS");
  });

  it("rejects public IPv4 addresses for Android HTTP pairing", () => {
    expect(() =>
      buildPairingPayload("android", "http://8.8.8.8:8420", "abc"),
    ).toThrow("Android HTTP 配对只允许私有局域网地址");
  });

  it("allows public HTTPS origins in Android mode", () => {
    expect(
      buildPairingPayload("android", "https://video.example.com", "abc"),
    ).toContain("framearchive://pair?");
  });

  it("normalizes the origin and safely encodes the token", () => {
    expect(
      buildPairingPayload(
        "browser",
        "https://VIDEO.Example.com:8443/",
        "a&b=c /中文",
      ),
    ).toBe(
      "framearchive://pair?v=1&server=https%3A%2F%2Fvideo.example.com%3A8443&token=a%26b%3Dc%20%2F%E4%B8%AD%E6%96%87",
    );
  });
});

describe("normalizePairingOrigin", () => {
  it.each([
    "http://10.0.0.1:8420",
    "http://10.255.255.255:8420",
    "http://172.16.0.1:8420",
    "http://172.31.255.255:8420",
    "http://192.168.0.1:8420",
    "http://127.0.0.1:8420",
    "http://127.255.255.255:8420",
  ])("allows Android HTTP pairing with private IPv4 origin %s", (origin) => {
    expect(normalizePairingOrigin(origin, "android")).toBe(origin);
  });

  it.each([
    "http://172.15.255.255:8420",
    "http://172.32.0.1:8420",
    "http://8.8.8.8:8420",
    "http://192.168.example.com:8420",
  ])("rejects Android HTTP pairing with non-private host %s", (origin) => {
    expect(() => normalizePairingOrigin(origin, "android")).toThrow(
      "Android HTTP 配对只允许私有局域网地址",
    );
  });

  it.each([
    "http://[fc00::1]:8420",
    "http://[fdff:ffff::1]:8420",
    "http://[fe80::1]:8420",
    "http://[febf:ffff::1]:8420",
    "http://[::1]:8420",
  ])("allows Android HTTP pairing with private IPv6 origin %s", (origin) => {
    expect(normalizePairingOrigin(origin, "android")).toBe(origin);
  });

  it.each([
    "http://[fbff:ffff::1]:8420",
    "http://[fec0::1]:8420",
    "http://[2001:4860:4860::8888]:8420",
  ])("rejects Android HTTP pairing with non-private IPv6 origin %s", (origin) => {
    expect(() => normalizePairingOrigin(origin, "android")).toThrow(
      "Android HTTP 配对只允许私有局域网地址",
    );
  });

  it("keeps brackets when normalizing an IPv6 URL", () => {
    expect(normalizePairingOrigin("http://[FC00:0:0::1]:8420/", "android")).toBe(
      "http://[fc00::1]:8420",
    );
  });

  it("allows HTTPS for both modes, including public hosts", () => {
    expect(normalizePairingOrigin("https://video.example.com/", "browser")).toBe(
      "https://video.example.com",
    );
    expect(normalizePairingOrigin("https://video.example.com/", "android")).toBe(
      "https://video.example.com",
    );
  });

  it("removes default ports while preserving explicit non-default ports", () => {
    expect(
      normalizePairingOrigin("https://VIDEO.Example.com:8443/", "browser"),
    ).toBe("https://video.example.com:8443");
    expect(normalizePairingOrigin("http://127.0.0.1:80/", "android")).toBe(
      "http://127.0.0.1",
    );
    expect(normalizePairingOrigin("https://example.com:443/", "browser")).toBe(
      "https://example.com",
    );
  });

  it.each<PairingMode>(["browser", "android"])(
    "rejects credentials, paths, queries, and fragments in %s mode",
    (mode) => {
      expect(() => normalizePairingOrigin("https://user:pass@example.com", mode)).toThrow(
        "配对服务器地址不能包含凭据",
      );
      expect(() => normalizePairingOrigin("https://example.com/api", mode)).toThrow(
        "配对服务器地址只能使用根路径",
      );
      expect(() => normalizePairingOrigin("https://example.com/?key=value", mode)).toThrow(
        "配对服务器地址不能包含查询参数",
      );
      expect(() => normalizePairingOrigin("https://example.com/#section", mode)).toThrow(
        "配对服务器地址不能包含片段",
      );
    },
  );

  it.each<PairingMode>(["browser", "android"])(
    "allows an omitted path or the root path in %s mode",
    (mode) => {
      expect(normalizePairingOrigin("https://example.com", mode)).toBe(
        "https://example.com",
      );
      expect(normalizePairingOrigin("https://example.com/", mode)).toBe(
        "https://example.com",
      );
    },
  );

  it.each<PairingMode>(["browser", "android"])(
    "rejects unsupported protocols and invalid URLs in %s mode",
    (mode) => {
      expect(() => normalizePairingOrigin("ftp://example.com", mode)).toThrow(
        "配对服务器地址必须使用 HTTP 或 HTTPS",
      );
      expect(() => normalizePairingOrigin("not a URL", mode)).toThrow(
        "配对服务器地址无效",
      );
    },
  );
});
