# archworld.execute

**在你的终端里，执行一个从 Unix 生长到 Arch 的字符世界。**

以 Mili《world.execute(me);》为配乐的 TUI / ASCII 音乐动画。
Unix 点亮字符世界，GNU 带来自由，Linux 内核加入；发行版各自生长，
Arch 让人亲手构建，AUR 让分享得到回应，最后把执行命令交给下一个人。

```bash
archworld.execute --me
# 同样可以：
world.execute --me
```

它不是在终端打开 MP4：Python 播放器输出预生成的真彩色字符帧，
mpv 提供音频时钟，真实 tmux 承载随剧情变化的会话名、窗口列表和日期。
片内的安装命令、进程和分屏是动画，不会执行那些命令。

## 安装与播放

### Arch Linux

从 [Releases](https://github.com/Lavandis/archworld.execute/releases) 下载
`archworld.execute-0.1.0-1-any.pkg.tar.zst`，在下载目录执行：

```bash
sudo pacman -U ./archworld.execute-0.1.0-1-any.pkg.tar.zst
archworld.execute --me
```

依赖为 `python`、`tmux`、`mpv`。安装后两个命令都能在任意目录运行。
旧的本地包 `world-execute` 与新包冲突，pacman 会提示移除旧包。
**目前尚未发布 AUR。**

### 解压运行（Linux）

从同一 Release 下载完整包 `archworld.execute-0.1.0.tar.gz`。
先使用发行版包管理器安装 Python 3.10+、tmux、mpv，再运行：

```bash
tar -xzf archworld.execute-0.1.0.tar.gz
cd archworld.execute-0.1.0
./archworld.execute --me
```

请选择我们上传的完整包；GitHub 自动生成的 Source code 压缩包不含音乐和动画数据。
当前主要在 Arch Linux 上验证，未验证所有 Linux 发行版，不提供原生 Windows 支持。

## 音乐与参数

完整 Release 包内置音频，直接播放即可。`--audio` 可以指定替代文件：

```bash
archworld.execute --me --audio /path/to/music.mp3
archworld.execute --me --no-tmux
archworld.execute --me --background '#171c22'
archworld.execute --help
```

- 全片约 211.91 秒，24 fps，从音乐开始；不包含视频的 fastfetch / 命令输入开场。
- 默认使用安装目录 `audio/music.mp3`；没有内置音频时回退到
  `${XDG_DATA_HOME:-$HOME/.local/share}/world-execute/music.mp3`（XDG_DATA_HOME 必须为绝对路径）。
- 替代音乐需要与原时间轴保持同版本、同起点，否则画面与声音可能错位。
- 播放结束自动退出，`Ctrl+C` 可提前结束。没有额外播放控制界面。

## 终端和 tmux

推荐支持 UTF-8、ANSI 真彩色的终端、等宽字体、深色背景，至少 **160 列 × 45 行**。
字符格宽高接近 1:2 时构图更接近原版；程序不修改终端字号或外部窗口大小。

- 原画布为 160×44 字符。小窗口等比缩小，大窗口居中留边，不重复放大字符行。
- 至少需要 80×22 的播放区域，tmux 状态栏另占空间；窗口过小时暂停，恢复后继续。
- 新建专用 tmux 会话，保留用户主题及额外状态栏元素；不改用户配置文件或已有窗口。
- 会话名称、当前分组的窗口列表与日期跟随剧情变化；历史窗口是休眠占位。
- 尾声日期逐渐对齐观众电脑的本地时间。退出后清理播放会话，从 tmux 内启动时切回原会话。
- 常规时钟字段可替换为剧情时间；不改写插件 shell 命令，缺少日期/时间时补充显示。
  复杂主题、动态插件重写状态栏仍可能影响显示。
- 背景使用终端默认背景，启动时查询背景色供淡出混色。不支持查询时可用 `--background` 指定。
  透明窗口背后的壁纸无法精确参与混色；播放中换主题请重新启动。

终端版不复现视频的辉光、扫描线等后期；小头像转为彩色半格像素。
缩小窗口会损失细节，终端字体和字符宽度设置也会影响观感。

## 开发与打包

本仓库只包含终端运行层，动画由原 Remotion 项目预生成；不需要 Node、Chromium 或 Remotion 来播放。
完整镜头源码和帧生成工具不在此仓库。克隆源码后，需从完整 Release 包取出 `data/` 与 `audio/` 才能播放或重新打包。

```text
world.execute          Python 入口
archworld.execute      同一入口的别名
src/player.py          字符播放、音频时钟、tmux 生命周期
Makefile               安装到 PREFIX，支持 DESTDIR
packaging/             发行压缩包与本地 Arch 打包脚本
tests/                 单元测试及真实 PTY / tmux / mpv 冒烟测试
```

```bash
python3 -m unittest discover -s tests
# 需要动画数据、音乐、tmux 和 mpv；音频通过 null 输出，无声：
python3 tests/smoke.py
# 重新打包，需要 data/ 和 audio/：
python3 packaging/prepare.py
cd dist
makepkg -f
```

测试覆盖尺寸适配、背景混色、音频路径、tmux 主题继承、窗口列表、暂停和退出恢复。
它们不替代全曲试听，也不保证所有终端/插件组合的兼容性。

卸载：`sudo pacman -R archworld.execute`。用户自行保存的音乐与配置不会被删除。

## 制作与许可

项目：Lavandis。视频主要由 Claude Opus 5.5 协助制作，Astra 协助审校、调整与终端版实现。

自有播放器代码采用 [MIT](LICENSE)。**音乐、第三方美术与商标不属于 MIT 授权范围**，
来源、署名和未确认的素材信息见 [NOTICE.md](NOTICE.md)。

灵感与技术参考：

- [MisakaZentai/world-execute-me-dsh-pv](https://github.com/MisakaZentai/world-execute-me-dsh-pv)
- [yym8224961/world.execute-me-ascii](https://github.com/yym8224961/world.execute-me-ascii)

遇到问题请附终端名称、tmux/Python 版本、窗口列数与行数及报错信息；
不要提交包含私密信息的个人配置。
