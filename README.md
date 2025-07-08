# Moeshare签到脚本
注意：因论坛检测请求头，该脚本需要Edge浏览器获取cookie
## 搭配青龙面板使用
* 导出浏览器Cookie (Copy as Node.js fetch)
* 青龙面板添加 MOESHARE_DAYSIGN 环境变量
* 添加 ql repo https://github.com/JamYiz/Moeshare_daysign.git 作为任务手动运行。
* 签到任务会自动定时运行。
## 如何使用fetch 命令
* 使用Edge浏览器按下 F12 键打开开发者工具。
* 找到并点击 Network（网络）选项卡。
* 在网络请求列表中，右键点击相关请求（通常是第一个或与页面加载最相关的请求）。
* 在弹出的菜单中，Copy as Node.js fetch（复制为 Node.js fetch）。
## 环境变量
* MOESHARE_DAYSIGN: Node.js fetch 字符串 (e.g. fetch("xxx", ...))
* TG_USER_ID(optional): @BotFather bot chat ID
* TG_BOT_TOKEN(optional): @BotFather bot token
