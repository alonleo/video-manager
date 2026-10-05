# 目录分类与视频移动

## 使用方式

- 影像库 →「目录分类」：根据每个本地视频来源的父目录自动分类；已有索引立即生效，无需迁移数据库或重新导入。
- 影像库 →「新建分类」：选择父目录并输入名称，真实创建本地目录；支持嵌套分类和中文名称。
- 视频播放页 →「移动视频」：选择要移动的本地来源以及目标分类；也可在移动窗口中新建目标分类。
- 分类选择写入 URL，可与搜索、收藏、标签和排序组合，刷新页面后保留。

## 分类语义

分类标识为「存储源 ID + 相对目录」，不会混淆不同盘或层级中的同名目录。根目录也属于一个分类。筛选只匹配视频的直接父目录，不递归包含子目录；同一个视频有多个可用本地来源时，可以出现在多个分类中，分类内按视频去重计数。空目录可显示并作为移动目标；不可用的索引目录保留提示。

分类直接读取本地存储目录，排除隐藏目录、系统回收目录及符号链接目录。单个存储源目录数达到 20000 时显示截断提示，同时仍包含已有视频索引对应的分类。大量目录的首次读取可能较慢，可手动刷新分类。

## 实际文件操作

- 同文件系统使用不覆盖目标的原子重命名；目标已有同名文件时拒绝。
- 跨文件系统使用目标目录内的临时文件进行分块复制、fsync、SHA-256 校验；校验源文件大小、时间戳和 inode 未变化后发布目标。数据库提交成功才清理原文件。
- 数据库提交失败时，同盘尝试移回原路径，跨盘移除本次生成的目标文件；原文件保留。跨盘原文件无法清理时返回明确提示，目标文件和索引仍保留。
- 更新来源的本地路径、存储源 ID 和扫描使用的相对 URI。重叠存储源中的重复引用会合并，避免移动后重新扫描产生重复来源。
- 视频 ID 不变，所以收藏、标签、播放进度以及按视频 ID 关联的字幕记录保留。只移动选择的视频文件；旁置的 SRT、VTT、封面等独立文件不自动移动。
- 扫描、移动和重命名共享进程间锁；视频有排队或进行中的处理任务时拒绝移动。格式转换入队也遵守该锁，避免读取移动中的旧路径。
- 路径限制在已启用本地存储源内，拒绝路径穿越、符号链接目标和无效目录名。远程网盘、URL 来源不支持实体移动。
- 大文件跨盘复制期间页面显示处理中。浏览器请求断开不会主动放弃正在执行的文件与索引更新；仍建议等待完成后离开。进程被强制终止或断电后的恢复不等同于数据库事务，可能需要重新扫描并检查残留的 .video-move-* 临时文件。

## 同时修正

1. 扫描子目录时，仅将该子树内未发现的来源标为不可用，避免影响其他分类。
2. 本地存储路径检查使用路径层级判断，防止相同字符串前缀的兄弟目录越界。

## API

- GET /api/categories：目录列表、直属视频计数、可用状态与读取提示。
- POST /api/categories：{storage_id, path, name}，返回新建分类。
- GET /api/videos?category=<storage_id>:<path>：按直接父目录筛选。
- POST /api/videos/{id}/move：{source_id, storage_id, path}，移动选择的来源。
- 视频列表和详情增加 categories，来源增加 storage_id。

## 验证

后端专项测试：backend/tests/test_directory_categories.py，19 项通过，覆盖中文目录、空目录、同名分类、路径穿越、符号链接、停用存储、同盘移动、跨存储移动、跨盘复制分支、数据库失败回滚、同名文件保护、处理任务互斥、重叠存储、保留收藏和进度、移动后重扫以及真实 HTTP 接口。

浏览器验收：scripts/directory-categories-audit-server.py + scripts/directory-categories-audit.mjs。在隔离 SQLite 和生成的测试视频上完成 7 项检查：筛选与 URL 持久化、真实新建目录、同名错误、Escape 关闭、真实移动及引用更新、移动后的 Range 206 播放、390px 布局与浏览器错误检查。没有使用真实视频库进行移动测试。

运行：

```bash
cd backend
.venv/bin/python -m pytest tests/test_directory_categories.py -q
```

```bash
# 两个终端；浏览器脚本使用已安装的 Playwright/Chrome
backend/.venv/bin/python scripts/directory-categories-audit-server.py
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs CHROMIUM_PATH=/usr/bin/google-chrome node scripts/directory-categories-audit.mjs
```

相关扫描与播放回归：test_directory_categories.py、test_transport.py、test_all_formats.py 合计 69 项通过、1 项跳过（32.79 秒）。前端 TypeScript 与 Vite 生产构建通过。

浏览器结果、桌面和手机截图位于 .run/directory-audit-*/；修改前代码备份为 .run/directory-categories-before-20261005.tar.gz。
