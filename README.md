# astrbot_plugin_qq_panel

在 AstrBot 里管理 **QQ 官方机器人的指令面板**（输入框上方可点击的指令）和 **单聊自定义菜单**（单聊窗口底部的菜单）。

面板和菜单通常在 WebUI 的插件配置页用可视化条目编辑，再用管理员指令推送到 QQ 开放平台；不需要在开放平台后台逐项操作。复杂的按人群面板和原始菜单结构可使用高级 JSON 配置。

对应官方文档：[自定义菜单与指令面板](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/menu-panel/)

---

## 安装

1. 把整个 `astrbot_plugin_qq_panel` 目录拷到 AstrBot 的插件目录：

   ```
   AstrBot/data/plugins/astrbot_plugin_qq_panel/
   ├── main.py
   ├── metadata.yaml
   ├── _conf_schema.json
   ├── requirements.txt
   ├── README.md
   └── qq_panel/
       ├── __init__.py
       ├── api.py
       ├── models.py
       └── sync.py
   ```

2. 在 WebUI 的「插件管理」里点重载（或重启 AstrBot），插件列表里应该能看到 `astrbot_plugin_qq_panel`。
3. 点插件的「配置」，在「指令面板按钮」和「单聊底部菜单」中编辑条目；默认条目可直接试用。
4. 私聊或群里发 `/qqpanel status`，确认凭证和适配器识别正确，然后执行 `/qqpanel sync`；菜单另行用 `/qqpanel menu sync` 推送。

依赖只有 `aiohttp`，AstrBot 本体已经带了，一般不需要额外安装。

### 前置条件

- 已经在 AstrBot 里配置并启用了 **QQ 官方机器人适配器**（`qq_official` 或 `qq_official_webhook`）。插件会直接复用它的 AppID / AppSecret，不用重复填。
- 机器人在 QQ 开放平台上**已获得指令面板 / 自定义菜单接口的调用权限**。没有权限时接口会返回 `11253`，需要到开放平台申请。
- 发指令的账号是 AstrBot 的**管理员**（本插件所有指令都限管理员）。

---

## 配置项

在 WebUI 的插件配置页里填写。

| 配置项 | 类型 | 说明 |
| --- | --- | --- |
| `panel_items` | 可视化条目列表 | 指令面板按钮。每条选择「指令按钮」或「链接按钮」，并勾选它出现的聊天场景。插件把同一场景的条目合并成一个面板。 |
| `menu_items` | 可视化条目列表 | 单聊底部菜单。支持发送指令、打开链接、折叠菜单和开关；条目可通过「放进哪个折叠菜单」组成二级菜单。 |
| `target_user_openids` | 列表 | 留空时单聊面板对所有用户生效；填写后，自动生成的单聊面板只对这些用户生效。 |
| `target_group_openids` | 列表 | 留空时群聊面板对所有群生效；填写后，自动生成的群聊面板只对这些群生效。 |
| `advanced_panels_json` | JSON 编辑器 | 可选的额外高级面板，会追加到可视化配置生成的面板后面。见「高级 JSON 配置」。 |
| `advanced_menu_json` | JSON 编辑器 | 可选的原始菜单配置。填入后会**完全取代** `menu_items`。见「高级 JSON 配置」。 |
| `platform_id` | string | 留空时自动查找已启用的 QQ 官方机器人适配器并复用其凭证。**只有同时启用了多个** QQ 官方机器人时才需要填（填 WebUI 消息平台列表里的那个 ID），否则插件会报错要你指定。 |
| `credentials.appid` | string（密文） | 一般不填。填了会覆盖适配器上的凭证。 |
| `credentials.secret` | string（密文） | 同上。两项要一起填才生效。 |
| `auto_sync_on_start` | bool | AstrBot 启动完成后自动推送一次面板配置。只写日志、不发消息，失败也不影响启动。 |
| `auto_sync_menu` | bool | 启动时连自定义菜单也推一次。仅在 `auto_sync_on_start` 也开启时生效。菜单改动很少而接口只有 5 QPM，建议保持关闭、需要时手动 `/qqpanel menu sync`。 |
| `delete_removed_panels` | bool | 某个自动生成的场景面板没有任何按钮，或高级面板被删除 / 设为 `enabled: false` 时，同步时顺手删掉线上对应面板。关闭时只在同步结果里提示，由你手动 `/qqpanel delete`。 |

> `credentials` 两项在 WebUI 里是密文显示。插件在日志和 `/qqpanel status` 的输出里只会显示遮罩后的 AppID（如 `102******456`），不会打印 AppSecret。

### 配置指令面板

在「指令面板按钮」中点击「添加条目」：

- **指令按钮**：填写实际可用的指令、说明文字，勾选单聊 / 群聊 / 频道文字子频道 / 频道私信等显示场景。
- **链接按钮**：填写按钮文字、以 `https://` 开头的网址、说明文字和显示场景。
- 同一场景的按钮会按配置顺序合并成一个面板；某条目没有勾选任何场景时不会被推送。
- 单聊和群聊的全局白名单分别填在 `target_user_openids` 和 `target_group_openids`。留空即对所有用户 / 群生效；填写后对应自动面板变为指定对象面板。

自动生成的面板键名为 `auto_c2c`、`auto_group`、`auto_channel`、`auto_dm`，由插件维护，无需填写。

### 配置单聊底部菜单

在「单聊底部菜单」中点击「添加条目」：

- **发送指令**：点击后把指令填入输入框。
- **打开链接**：点击后跳转到 `https://` 链接。
- **折叠菜单**：填写一个唯一的菜单文字；其他指令或链接条目的「放进哪个折叠菜单」填写相同文字，即可放进它的二级菜单。
- **开关**：只能放在一级菜单。用户打开后，后续消息的 `ext` 中会带上 `<switch_id>=1`；实际业务处理由其他插件完成。

菜单只在单聊窗口底部展示，所有用户看到的内容一致。

---

## 指令

全部指令都需要**管理员**权限。指令组别名：`/指令面板`（等价于 `/qqpanel`）。

| 指令 | 说明 |
| --- | --- |
| `/qqpanel status` | 凭证来源、AppID（已遮罩）、token 是否已缓存、配置解析结果、本插件已托管的 `key → panel_id`、上次同步时间 |
| `/qqpanel preview` | **只做本地校验**，打印将要推送的面板与菜单摘要，不调用任何平台接口。改完配置先跑这个 |
| `/qqpanel sync [key]` | 把 `panel_items` 和额外高级面板推送到平台。带 `key` 时只同步那一个面板 |
| `/qqpanel list [scope]` | 列出平台上已生效的面板。不带参数时四个场景全查；`scope` ∈ `c2c` / `group` / `channel` / `dm` |
| `/qqpanel show <panel_id>` | 面板详情，含面板元素和关联的 openid 列表 |
| `/qqpanel delete <panel_id>` | 删除线上面板，并清掉本地映射 |
| `/qqpanel target <add\|del> <key 或 panel_id> <openid...>` | 手动增删面板关联对象，多个 openid 用空格分隔，内部自动按 20 个一批提交 |
| `/qqpanel menu show` | 查看线上当前生效的自定义菜单 |
| `/qqpanel menu sync` | 把 `menu_items`（或 `advanced_menu_json`）推送到平台（整体覆盖） |
| `/qqpanel menu clear` | 清空线上自定义菜单 |
| `/qqpanel help` | 指令一览 |

### 同步是幂等的

插件在本地记住 `key → panel_id` 的映射（存在插件 KV 存储里，不可用时回退到插件数据目录下的 `state.json`）。所以：

- 反复 `/qqpanel sync` 不会把面板越建越多，已存在的走「修改」。
- 可视化配置生成的面板按场景固定使用 `auto_<scope>` 作为键；删除某场景的全部按钮，等同于让该面板变成待清理状态。
- 改高级面板的 `key` 等于**新建一个面板**（旧的会变成「待清理」）。
- 有人在开放平台后台把面板删了，下次同步会自动重建。
- `target_type: specific` 的面板，插件会拿线上的 openid 列表和配置做差集，只发变化的部分（`add` / `del`）。

`/qqpanel target` 是直接改线上关联对象的应急口子。下次同步仍以配置为准：自动生成的单聊 / 群聊面板分别看 `target_user_openids` / `target_group_openids`，高级面板看自己的 openid 列表。手动改过后，记得同步更新相应配置。

---

## 高级 JSON 配置

日常配置使用可视化条目即可。只有需要不同人群使用不同按钮、单个高级面板的备注 / 启用状态，或希望直接提交 QQ 原始菜单结构时，才使用以下 JSON 字段。

### `advanced_panels_json`

顶层是数组（也接受 `{"panels": [...]}`）。其面板会追加到 `panel_items` 生成的自动面板之后；`key` 不能以 `auto_` 开头，也不能与其他高级面板重复。

```json
[
  {
    "key": "vip_groups",
    "scope": "group",
    "target_type": "specific",
    "group_openids": ["openid_group_001", "openid_group_002"],
    "remark": "VIP 群专用面板",
    "items": [
      { "type": "command", "name": "/draw", "desc": "AI 绘图" }
    ]
  },
  {
    "key": "group_maintenance",
    "enabled": false,
    "scope": "group",
    "target_type": "all",
    "remark": "临时停用的群面板",
    "items": [
      { "type": "command", "name": "/help", "desc": "查看可用指令" }
    ]
  }
]
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `key` | 是 | **本地标识**，不会发给平台，用来记住线上的 `panel_id`。不要重复，也不要以 `auto_` 开头。 |
| `scope` | 是 | `c2c` 单聊 / `group` 群聊 / `channel` 文字子频道 / `dm` 频道私信 |
| `target_type` | 否 | `all`（默认）全局生效；`specific` 只对指定对象生效 |
| `user_openids` | 视情况 | 仅 `c2c` + `specific` 用，且必须非空 |
| `group_openids` | 视情况 | 仅 `group` + `specific` 用，且必须非空 |
| `enabled` | 否 | 默认 `true`。`false` 表示暂时不推送这个面板 |
| `remark` | 否 | 备注，≤255 字符，不展示给用户 |
| `items` | 否 | 面板元素，最多 20 个 |

### 高级面板元素字段

| 字段 | 说明 |
| --- | --- |
| `type` | `command` 或 `link` |
| `name` | 展示文本，≤14 字符（一个汉字算 2 个）。`type: command` 时点击后会填入输入框，所以要写成真正的指令，如 `/help` |
| `desc` | 可选说明，≤30 字符（汉字算 2 个） |
| `only_admin` | 可选，`true` 表示只对群管理员展示 |
| `link` | `type: link` 时必填，必须以 `https://` 开头 |

`channel` 和 `dm` 只支持 `target_type: all`。`specific` 场景下 `c2c` 只能用 `user_openids`、`group` 只能用 `group_openids`，写反了插件会直接拦下来。

### `advanced_menu_json`

该字段一旦填入，就会**完全取代**可视化 `menu_items`，不会合并。顶层可写 `{"items": [...]}`，也可直接写数组：

```json
{
  "items": [
    { "type": "send_message", "name": "帮助", "send_message": "/help" },
    { "type": "send_message", "name": "新对话", "send_message": "/new" },
    { "type": "link", "name": "使用文档", "link": "https://astrbot.app" },
    {
      "type": "menu",
      "name": "更多",
      "sub_menu_items": [
        { "type": "send_message", "name": "重置对话", "send_message": "/reset" },
        { "type": "send_message", "name": "查看用量", "send_message": "/stats" }
      ]
    },
    {
      "type": "switch",
      "name": "联网搜索",
      "switch": { "switch_id": "search", "default": false }
    }
  ]
}
```

| `type` | 行为 |
| --- | --- |
| `send_message` | 点击后把 `send_message` 的内容填入输入框 |
| `link` | 跳转到 `link`（必须 `https://` 开头） |
| `menu` | 折叠子菜单，`sub_menu_items` 最多 5 个、`name` ≤14 字符、只能是 `send_message` / `link`，不能再嵌套 |
| `switch` | 开关。用户打开后，其消息的 `ext` 里会带上 `<switch_id>=1`，插件可据此改变行为 |

一级 `items` 最多 10 个，`name` ≤10 字符（汉字算 2 个）。`{"items": []}` 等于清空菜单（也可以直接用 `/qqpanel menu clear`）。

---

## 平台限制

插件会在本地先把这些都校验一遍，不合规的配置根本不会发出请求，`/qqpanel preview` 会一次性列出所有问题。

| 项 | 限制 |
| --- | --- |
| 面板数量 | 一个机器人最多 **20** 个 |
| 面板元素 | 单面板最多 **20** 个 |
| 面板元素 `name` | ≤14 字符（汉字算 2） |
| 面板元素 `desc` | ≤30 字符（汉字算 2） |
| `remark` | ≤255 字符 |
| `link` | 必须 `https://` 开头 |
| `target_type: specific` | 仅 `c2c` / `group` 支持；`channel` / `dm` 只能 `all` |
| openid 批量 | 创建面板时一次最多带 20 个；`/target` 接口一次最多 20 个（插件自动分批） |
| 面板详情返回的 openid | 最多 1000 条 |
| 菜单一级项 | 最多 10 个，`name` ≤10 字符 |
| 菜单子项 | 最多 5 个，`name` ≤14 字符，仅 `send_message` / `link` |

### 频率限制（QPM）

插件按接口分组做了最小请求间隔，不需要自己控制节奏，但面板多的时候一次 `sync` 会慢一些（写操作 10 QPM → 每次间隔 6 秒）。

| 接口 | 限制 |
| --- | --- |
| 面板列表 / 详情（GET） | 30 |
| 面板创建 / 修改 / 删除 | 10 |
| 面板关联对象（PUT `/target`） | 60 |
| 菜单查询（GET） | 30 |
| 菜单修改（PUT） | 5 |

---

## 错误码对照

平台约定**以响应体里的 `err_code` 判断失败**，`message` 文案可能随时变。插件把 `err_code`、排查建议和 `trace_id` 一起回给你，报 bug / 提工单时把 `trace_id` 一起带上。

| `err_code` | 含义与处理 |
| --- | --- |
| `100001` | 请求过于频繁，降低调用频率后重试 |
| `100007` | AppID 无效，或机器人状态不正常（被封禁 / 已删除） |
| `100016` | AppID 或 AppSecret 不正确，检查与开放平台管理端是否一致 |
| `10004` | AppID 对应的机器人不存在 |
| `11253` | **机器人未获得该接口的调用权限，需要在开放平台申请** |
| `11254` | 该接口已被封禁 |
| `11265` | 机器人已被封禁 |
| `40030001` | 参数错误 |
| `40030006` | 指令面板不存在（插件遇到这个会自动清掉本地映射并重建） |
| `40030008` | URL 格式错误，链接必须 `https://` 开头 |
| `40030009` | 面板操作进行中（并发冲突），插件会自动退避重试 |
| `40030011` | `scope` 不合法 |
| `40030012` | `target_type` 不合法（`channel` / `dm` 只能 `all`） |
| `40030013` | 超出数量限制 |
| `40030014` | 菜单类型不合法 |
| `40030015` | 面板元素类型不合法 |
| `40030016` | 必填字段缺失 |
| `40030017` | `op` 不合法（仅 `add` / `del`） |
| `40030018` | 当前场景不支持此操作 |
| `40030020` | 内容存在安全风险，检查是否含敏感信息 |
| `40030021` | 全局面板不支持指定关联对象，把 `target_type` 改成 `specific` |

---

## 真机验证步骤

本地跑不了真实接口（需要真实 AppID / AppSecret），按下面的顺序过一遍：

1. 把整个目录拷到 AstrBot 的 `data/plugins/astrbot_plugin_qq_panel`，在 WebUI 插件管理里重载。
2. 在 WebUI 里配置「指令面板按钮」和「单聊底部菜单」（可以先直接使用默认条目）。
3. 私聊或群里发 `/qqpanel status`，确认**凭证来源**指向了正确的适配器、AppID 遮罩后的首尾对得上。
4. `/qqpanel preview`：确认本地校验通过、面板内容是你想要的。
5. `/qqpanel sync`：推送面板，查看返回的「新建 / 更新」列表。
6. `/qqpanel list`：核对线上状态，面板应该都带上「← 本插件托管」。
7. 在 QQ 客户端里打开与机器人的单聊 / 群聊，看输入框上方是否出现指令面板。**平台侧有缓存，可能要等几分钟或退出重进会话才能看到。**
8. `/qqpanel menu sync` 之后，在单聊窗口底部确认菜单出现；点一下 `switch` 项，再发条消息，检查消息的 `ext` 里是否带上 `<switch_id>=1`。

---

## 常见问题

**AppID / AppSecret 从哪来？我没填过。**
插件默认从 AstrBot 已启用的 QQ 官方机器人适配器上直接读（`qq_official` / `qq_official_webhook` 的配置里那两项）。所以只要你的 QQ 官方机器人已经能正常收发消息，就不用再填一遍。`/qqpanel status` 里的「凭证来源」会告诉你这次用的是哪个适配器。

**报「检测到多个 QQ 官方机器人适配器」怎么办？**
你同时启用了多个 QQ 官方机器人，插件不知道该管哪个。把要管理的那个适配器 ID（WebUI 消息平台列表里显示的 ID）填到插件配置的 `platform_id` 里。

**指令都执行成功了，但 QQ 里看不到面板。**
按这个顺序排查：

1. `/qqpanel list`：面板是不是真的在线上？在，就说明推送成功了，问题在客户端展示。
2. **看 `err_code`，不要看 `message`。** 如果同步返回了失败项，`err_code` 才是判断依据。`11253` 是最常见的：机器人没有这个接口的权限，要去开放平台申请。
3. 平台侧有缓存，等几分钟，或退出会话重进。
4. 确认场景对得上：单聊看不到就检查指令按钮是否勾选 `c2c`，群里看不到就检查 `group`。
5. 指定对象面板只对配置的 openid 生效；用 `/qqpanel show <panel_id>` 查看关联对象里有没有你自己。
6. `only_admin: true` 的元素只对群管理员展示。

**面板元素点了之后没反应？**
指令按钮会把「指令」原样填入输入框，它必须是机器人真的能识别的指令。写 `帮助` 而 AstrBot 注册的是 `/help`，点了就是发一句「帮助」，机器人不认。

**我改了配置，`sync` 之后线上多了个面板。**
通常是改了高级面板的 `key`。`key` 是插件认高级面板的唯一依据，改了就等于新建。旧面板会在同步结果的「待清理」里列出来，用 `/qqpanel delete <panel_id>` 清掉，或者把 `delete_removed_panels` 打开让插件自己清。

**能自动把 AstrBot 注册的指令同步成面板吗？**
不能，本插件只按 `panel_items` 和可选的高级面板配置推送。面板位置只有 20 个，而 AstrBot 装几个插件就能有上百条指令，自动同步意义不大。

**`switch` 类型的开关怎么用？**
用户打开开关后，用户之后发的消息的 `ext` 字段里会带上 `<switch_id>=1`。插件只负责把开关配上去，读取和响应需要在业务插件里处理。

---

## 许可

本项目采用 [MIT](https://github.com/xiaoruange39/astrbot_plugin_qq_panel/blob/main/LICENSE) 许可证。

## QQ群

123180736
