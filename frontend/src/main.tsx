import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ConfigProvider } from "@arco-design/web-react";
import zhCN from "@arco-design/web-react/es/locale/zh-CN";
import "@arco-design/web-react/dist/css/arco.css";

import App from "./App";
import LibraryPage from "./pages/LibraryPage";
import PlayerPage from "./pages/PlayerPage";
import DubbingPage from "./pages/DubbingPage";
import PhotoPage from "./pages/PhotoPage";
import "./photos.css";
import StoragesPage from "./pages/StoragesPage";
import JobsPage from "./pages/JobsPage";
import "./index.css";
import "./subtitles.css";
import SettingsPage from "./pages/SettingsPage";
import DownloadsPage from "./pages/DownloadsPage";
import DownloadSettingsPage from "./pages/DownloadSettingsPage";
import "./components/player/player-controls.css";
import DeviceAccessGate from "./components/DeviceAccessGate";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 本地应用，数据变化只来自自己，不需要频繁重取
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30_000,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        // 帧藏色板：纸色底、墨绿字、砖红强调；去掉 Arco 默认蓝
        token: {
          colorPrimary: "#b94327",
          colorTextBase: "#24372f",
          colorBgBase: "#f6f5f0",
          borderRadius: 6,
          fontFamily:
            "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
        },
      }}
    >
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <DeviceAccessGate><Routes>
            <Route element={<App />}>
              <Route index element={<LibraryPage key="videos" />} />
              <Route path="photos" element={<LibraryPage key="photos" mediaType="photo" />} />
              <Route path="photos/:id" element={<PhotoPage />} />
              <Route path="videos/:id" element={<PlayerPage />} />
              <Route path="videos/:id/dubbing" element={<DubbingPage />} />
              <Route path="storages" element={<StoragesPage />} />
              <Route path="jobs" element={<JobsPage />} />
              <Route path="downloads" element={<DownloadsPage />} />
              <Route path="settings/downloads" element={<DownloadSettingsPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="settings/playback" element={<SettingsPage />} />
              <Route path="settings/ai" element={<SettingsPage />} />
              <Route path="settings/dubbing" element={<SettingsPage />} />
              <Route path="settings/devices" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes></DeviceAccessGate>
        </BrowserRouter>
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
);
