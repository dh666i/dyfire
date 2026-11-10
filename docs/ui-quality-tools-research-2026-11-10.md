# 火花 UI 品质工具调研 · 2026-11-10

后续已完成本地接入与精修，实际效果、范围和验证结果见 [UI 品质提升完成清单](ui-quality-implementation-2026-11-10.md)。下文保留调研时的判断与待办状态。

建议组合：**Impeccable 打磨视觉，Emil Kowalski 的设计技能打磨动效，现有 Motion 负责实现；Base UI 作为交互组件基础候选，Motion Primitives 和 React Bits 按组件取用。**

沿用已确认的暖橙、圆角、玻璃导航及 React + Vite。当前项目使用 React 19、Motion 12.43.0 和普通 CSS。以下选型以这套实际环境为基础。

本轮完成资料、源码、维护状态、许可证和依赖要求调研；没有安装新的组件库或技能，也没有改动应用代码或部署。上一轮已有改进见 [UI 体验完成清单](ui-experience-implementation-2026-11-10.md)。

**最值得关注的组件与动效资源**

| 资源与官方入口 | 适合火花的具体位置 | 实际价值 | 接入判断 |
| --- | --- | --- | --- |
| [Base UI](https://base-ui.com/react/overview/about) · [Drawer 示例](https://base-ui.com/react/components/drawer) | 好友选择、菜单、下拉选择、任务编辑、手机底部面板 | 统一焦点、键盘、弹层嵌套和触摸行为，同时保留本站外观 | 优先做一个选择器和一个手机面板的试点；支持普通 CSS，React 19 匹配 |
| [Motion Primitives](https://motion-primitives.com/) · [Transition Panel](https://motion-primitives.com/docs/transition-panel) · [Morphing Dialog](https://motion-primitives.com/docs/morphing-dialog) | 详情展开、分段切换、表单内容变化 | 打磨元素从哪里出现、如何退回，以及内容变化时的连续感 | 选取过渡模式，转换为项目现有 CSS；不整套复制它自己的弹窗系统 |
| [React Bits Glass Surface](https://reactbits.dev/components/glass-surface) | 顶部导航、手机 Dock、小范围悬浮控件 | 玻璃的边缘反光、背景折射和层次感 | 做小范围效果对比，保留现有 RubberSegment；提供普通磨砂降级 |
| [Sonner](https://sonner.emilkowal.ski/) | 保存、复制、刷新、批量操作等全局反馈 | 一条通知连续表达处理中、成功或失败，可带后续操作 | 当前 Toast 已有改进；仅在统一全站通知服务时评估替换，不叠加第二套提示 |
| [NumberFlow](https://number-flow.barvian.me/) | 今日完成数量、后台统计数字 | 真实数值变化时平滑过渡，避免整块内容闪换 | 可选的小范围增强；初次显示实际值，后续已确认变化才动画 |
| [Animate UI](https://animate-ui.com/docs) | 按钮、图标、Tabs、自动高度过渡 | 提供完整的动态组件范例，适合比较反馈和状态变化的效果 | 最新版与当前 Motion 主版本不一致，列入隔离验证；不直接整库安装 |
| [Lucide](https://lucide.dev/guide/packages/lucide-react) | 确实存在风格不一致的功能图标 | 统一线条粗细、视觉大小和语义 | 按需取用；保留品牌 Logo，避免为了统一而替换已经一致的图标 |
| [Storybook](https://storybook.js.org/docs/writing-tests/visual-testing) | 开发时的组件状态展示与视觉回归 | 一处比较深浅色、手机、加载、错误、长文字和空数据，减少修一处坏另一处 | 可先利用现有预览和浏览器测试建立状态页，再判断是否需要完整引入 |

Base UI 提供行为基础，本身不决定美观程度。字号、间距、材质、颜色及动画仍由火花的设计系统控制。当前已有自定义弹层、选择器和提示组件，因此采用时应替换它们的内部基础，保留业务接口，避免多套弹层各自管理滚动和焦点。

Base UI 当前 Drawer 文档包含拖拽、停靠位置、嵌套和虚拟键盘适配。普通详情或好友选择可以评估这些能力；**抖音远程画面、滑块验证码等区域必须隔离关闭手势**，不能让用户拖验证码时把整个窗口关闭。不需要手势的绑定窗口继续采用 Dialog 更合适。

**最适合这次需求的技能**

| 技能资源 | 推荐的能力 | 对火花的用途 |
| --- | --- | --- |
| [Impeccable](https://impeccable.style/docs/) · [源码](https://github.com/pbakaus/impeccable) | critique、typeset、layout、distill、polish、adapt、harden | 审查已有页面的视觉层级、字体、留白、冗余线条和移动端布局；记录已确认的设计规则，防止后续风格漂移 |
| [Emil Kowalski Skills](https://github.com/emilkowalski/skills) | emil-design-eng、review-animations、choreograph、animation-performance | 统一进入与退出节奏、按钮即时响应、可中断动画，以及多个元素之间的先后关系 |
| [Emil 的 Apple Design 与手机技能](https://github.com/emilkowalski/skills/tree/main/skills) | apple-design、gestures、mobile-native、break-ui | 对应你关心的玻璃质感、视觉居中、触摸手感、软键盘、安全区、长名称和极端数据；apple-design 是作者对 Apple 设计原则的整理，并非 Apple 官方技能 |
| [UI Skills](https://www.ui-skills.com/) · [源码](https://github.com/ibelick/ui-skills) | baseline-ui、无障碍与动画性能检查 | 作为技术质量检查，约束看不清、点不到、焦点丢失和卡顿；美感仍需要逐页视觉调整 |
| [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | 配色、字体和布局模式检索 | 当某个页面缺少设计参考时定向检索；已有认可风格，不需要再次生成全站设计系统 |

这些技能属于指导和检查方法，组件库才提供运行在网页里的代码。技能不能代替浏览器查看和人工判断，也不应一次加载多套互相冲突的设计规范。

本轮仅研究技能内容，没有将其嵌入的执行要求作为当前任务指令。若采用 Impeccable，优先选取设计审查能力；其 Live 模式文档包含另起代理及绕过审批/沙箱的回退执行机制，本轮没有启动这种自动化流程。

**兼容性和维护核对**

以下版本是本次调研时 registry 返回的值，接入前应再次确认并固定版本。

| 候选 | 核实结果 | 对选型的影响 |
| --- | --- | --- |
| @base-ui/react 1.9.0 | 支持 React 17/18/19；MIT；可用普通 CSS | 与当前技术栈相容，仍需要验证现有弹层、表单与主题集成 |
| @animate-ui/react 2.4.3 | React 19、Motion ^14.0.0；其他多项集成为可选 peer | 当前 Motion 12.43.0 不满足要求，不能直接认定可以接入；不为外观单独强升主版本 |
| Motion Primitives | MIT；README 标记 beta；源码依赖 Motion 和 Tailwind 风格类 | 动效范例有参考价值，选择性适配，不引入整套样式与弹窗行为 |
| Sonner 2.0.8 | MIT；支持 React 18/19 | 适合作为唯一的通知实现候选 |
| @number-flow/react 0.6.2 | MIT；支持 React 18/19 | 兼容性匹配，小范围实测后再决定 |
| React Bits Glass Surface | JS-CSS registry 没有列出依赖；实现含 SVG/WebGL 及磨砂降级路径 | 无依赖不等于无 GPU 成本，需要测低性能手机与 Safari |
| React Bits / Animate UI | 当前文件为 MIT + Commons Clause，可用于商业应用，限制重新销售或分发组件本身 | 接入商业站点可行，保留相应许可声明；不要当成纯 MIT 组件包重新分发 |
| Vaul | 官方 README 明确写有停止维护说明 | 新增手机抽屉不优先选择它，优先评估 Base UI Drawer |

依据：[Base UI npm](https://www.npmjs.com/package/@base-ui/react)、[Animate UI npm](https://www.npmjs.com/package/@animate-ui/react)、[Sonner npm](https://www.npmjs.com/package/sonner)、[NumberFlow npm](https://www.npmjs.com/package/@number-flow/react)、[React Bits registry](https://reactbits.dev/r/GlassSurface-JS-CSS.json)、[Vaul README](https://github.com/emilkowalski/vaul/blob/main/README.md)、[React Bits 许可](https://github.com/DavidHDev/react-bits/blob/main/LICENSE.md)、[Animate UI 许可](https://github.com/imskyleen/animate-ui/blob/main/LICENSE.md)。

**继续提升品质的落地清单**

以下是进一步打磨，不应与上一轮已经完成的菜单、加载、表单和热力图改进混为一谈。

| 任务 | 能看到或感受到的效果 | 是否完成 |
| --- | --- | --- |
| 固化本站视觉规则，逐页做视觉审查 | 标题、姓名、说明文字、按钮和留白更有秩序，清除剩余多余装饰；保留暖橙与圆角 | 工具与方法已选，下一轮逐页执行 |
| 统一按钮完整状态 | 点下立即响应，提交中保留按钮宽度，成功短确认，失败保留输入并指明可重试 | 已有局部反馈，待统一覆盖 |
| 精修任务与账号详情的空间过渡 | 从点击位置进入详情，关闭回到原位置；连续操作时能自然打断动画 | 已有开关动画，进一步过渡待做 |
| 手机好友选择与编辑面板试点 | 键盘弹起仍能操作，滚动与关闭手势不冲突，未保存修改受保护 | Base UI 能力已核实，接入与真机验证未做 |
| 全站使用一个通知服务 | 保存、复制、刷新反馈位置与节奏一致，不重复叠通知，处理结果延续同一条提示 | 现有 Toast 已改善，服务统一待做 |
| 小范围玻璃材质对比 | 导航和 Dock 边缘更自然、层次更细腻，同时保持文字清楚 | 候选源码已核实，实际渲染和性能对比未做 |
| 真实统计值变化过渡 | 刷新后完成数自然变化，页面不从零假计数、不整块闪动 | 当前已去除假计数；后续变化动画为可选项 |
| 建立组件状态展示与截图基线 | 同时核对两种主题、不同宽度、加载/失败/空数据/长文本，后续修改更容易发现退步 | 已有浏览器覆盖，专用状态展示与基线待补 |

执行顺序建议：先视觉审查和按钮反馈，再统一弹层/通知行为，最后做玻璃与数字等可选效果。每一项用同一页面前后对照验收，避免仅凭安装成功或构建通过宣称视觉提升。

技术接入点：`frontend/src/experience-components.jsx` 已承载 Toast、用户菜单、骨架和热力图；`frontend/src/app.jsx` 仍有共享 Drawer 与多处提示状态；`frontend/src/select-overlay.js` 是现有下拉适配入口；`frontend/src/binding-components.jsx` 涉及真实远程画面与验证码输入，必须保留业务行为。

**可以看，但当前优先级较低的资源**

- [Magic UI](https://magicui.design/docs/components/animated-list) 和 [Aceternity UI](https://ui.aceternity.com/explore)：适合登录、宣传页或空态的少量视觉亮点。持续发光边框、粒子与大面积 3D 效果不适合作为任务和管理页的常态。
- [Recharts](https://recharts.github.io/en-US/api/ResponsiveContainer/)：如果监控增加多指标对比、缩放和更复杂的数据探索再评估；当前曲线组件刚改善，无需仅为更换库重新实现。
- [Motion 官方 Base UI 指南](https://motion.dev/docs/base-ui)：适合把已有 Motion 能力接到无样式交互组件上，比同时引入多套动画运行时更贴近当前项目。

**核实范围**

已读取官方文档、公开仓库和 npm 元数据，并检查部分组件源码。Motion Primitives 文档抓取返回 403，改为核对其官方 GitHub 源码；没有声称已实际体验它的在线示例。外部组件尚未在本站运行，没有新增包体积、帧率、低端手机或真实 Safari 的测试结论。维护活跃和版本兼容只是入选条件，不等同于生产验收。

调研证据保存在本地 `.artifacts/ui-tools-research-2026-11-10/`，包含仓库信息、源码片段、许可证和兼容性检查结果。
