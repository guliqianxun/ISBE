'use strict';
// The ISBE console over /api. No build step; every
// value from the API goes through esc() before it reaches innerHTML.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const enc = encodeURIComponent;
  // Links from sources (RSS especially) are untrusted: only http(s) becomes a link.
  const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '');

  // ---------- preferences (per browser) ----------
  const PRESETS = { signal: [175, 1], glacier: [235, 1], iris: [290, 1], coral: [22, 1], ember: [62, 1], moss: [145, 1], graphite: [250, 0] };
  const DEFAULTS = { theme: 'signal', hue: 200, mode: 'dark', paper: 'paper', lang: (navigator.language || '').startsWith('zh') ? 'zh' : 'en' };
  const loadPrefs = () => {
    try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem('isbe.ui') || '{}') }; } catch { return { ...DEFAULTS }; }
  };
  let prefs = loadPrefs();
  const savePrefs = () => { try { localStorage.setItem('isbe.ui', JSON.stringify(prefs)); } catch { /* private mode */ } };
  let panel = false;

  const L = {
    zh: {
      articleStoredUnavailable: '已保存的原文暂时无法读取，请稍后重试。', articleExpand: '展开原文', articleCollapse: '收起原文', articleLoading: '正在读取原文…', articleUnavailable: '原网站暂时未能响应。请点击报道标题查看，或稍后重试。', articleUnsupported: '这个网站的正文暂未适配，请点击报道标题阅读原文。', articleReadAt: '原网页正文 · 读取于 {time}', articleSavedAt: '已保存原文 · 获取于 {time}',
      mediaWeekly: '生成材料汇编', mediaWeeklyTopic: '归入周报主题', mediaWeeklyOwnTopic: '{name}（本检索方案）', mediaWeeklyEnd: '周末日期（星期日）', mediaWeeklyWindow: '本期：{start} 至 {end}', mediaWeeklyNote: '按日期汇编全部检索材料，供编写行业周报时选用。内容沿用搜索摘要，尚未按议题整理；日期待核对及冲突材料列入附录。', mediaWeeklyInvalid: '请选择周报主题，以及今天之前已结束的星期日。', mediaWeeklyCreated: '材料汇编已生成。', mediaWeeklyOpen: '打开材料汇编', mediaWeeklyCounts: '本周 {weekly} · 前期背景 {background} · 周后 {newer} · 日期待核对 {unknown} · 日期冲突 {conflict} · 其他范围 {outside}', mediaWeeklyClose: '收起汇编设置',
      webSearchRetryBatch: '重新核对本批',
      webSearchRecheck: '重新核对日期', webSearchContinue: '继续核对余下 {n} 条', webSearchRecheckStats: '本批核对 {checked} 条 · 余下 {remaining} 条', webSearchPlanContext: '当前方案：{name}', webSearchPlanWindow: '方案窗口：{start} 至 {end}', webSearchRetainedDate: '最近日期核对未完成，保留上次记录。',
      webSearchRegion: '地域（可选）', webSearchInstitution: '机构（可选）', webSearchRegionHint: '地域和机构会以引号词组附加在每条检索词后；搜索服务无法按它们过滤，结果需自行核对。', webSearchSyntax: '语法与 Google 学术相同：空格表示全部包含；大写 OR 表示任一；"短语" 精确匹配；-词 排除；site:域名；intitle:词。例：上海 "绿色金融" ESG OR 可持续 -招聘。不支持 author:、source:。', webSearchUnexecuted: '搜索服务未按这些条件过滤，请在结果中核对：{items}', webSearchListSep: '、', webSearchC_terms: '检索词', webSearchC_phrase: '精确短语', webSearchC_boolean: 'OR 组合', webSearchC_exclusion: '排除词', webSearchC_site: '限定网站（site:）', webSearchC_title: '标题限定（intitle:）', webSearchC_institution: '机构', webSearchC_region: '地域', webSearchC_language: '语言', webSearchC_publication_window: '发布日期窗口',
      articleDns: '原文地址解析', articleDnsSystem: '系统默认', articleDnsCloudflare: 'Cloudflare 加密 DNS', articleDnsNote: '用于读取原文及发布日期；选择 Cloudflare 会向其发送公开域名，可解决虚拟 IP 导致原文无法读取的问题。',
      webSearchNoDateMatches: '没有符合当前日期筛选的材料。',
      webSearchWindow: '本次检索窗口', webSearchDateRead: '已读取原文日期信息', webSearchDateNotRead: '原文日期尚未核对', webSearchDateReadFailed: '原文暂未读到', webSearchLimited: '本次只取首批结果，可能还有更多报道。', webSearchEnginesUnavailable: '{n} 个搜索引擎未能响应。', webSearchSearxngWindow: '可用时向 SearXNG 提示近月或近年范围，上游可能不支持；结果仍需按原文起止日期核对。每次最多核对 20 篇原文，其余及范围外结果均保留。', webSearchBaiduRemoved: '百度（已停用）', webSearchBaiduRemovedHint: '百度搜索已停用。请把搜索服务改为 SearXNG 并保存后再检索；已有材料仍可查看和重核日期。',
      webSearch: '联网检索', webSearchNote: '围绕主题寻找各家媒体与公开网站的报道，按原文日期归入材料收件箱。', webSearchPlans: '检索方案', webSearchNew: '新建方案', webSearchEmpty: '尚未保存检索方案。可先从右侧示例开始。', webSearchName: '方案名称', webSearchId: '方案标识', webSearchProvider: '搜索服务', webSearchQueries: '检索词（每行一条）', webSearchQueriesHint: '最多 8 条，每条最多 200 字；每条分别检索。可添加英文词或 site:域名，具体语法由搜索服务决定。', webSearchStart: '开始日期', webSearchEnd: '结束日期', webSearchLanguage: '搜索语言', webSearchMax: '每条检索词最多结果数', webSearchSave: '保存方案', webSearchRun: '保存并检索', webSearchRunSaved: '开始检索', webSearchSaved: '检索方案已保存。', webSearchInvalid: '请填写方案标识、名称、主题、1–8 条检索词及有效日期范围；每条最多 200 字，结果数为 1–20。', webSearchUnavailable: '此搜索服务尚未配置。可以先保存方案，配置后再检索。', webSearchSettings: '配置搜索服务', webSearchReady: '服务已配置；连接将在检索时验证。', webSearchWindowNote: '先向搜索服务限定时间，再核对原文日期。日期不明、超出范围及冲突的结果会保留供你查看。', webSearchManual: '按需执行；保存方案不会自动安排定时任务。检索与日期核对不调用 LLM。', webSearchRecent7: '近 7 天', webSearchRecent30: '近 30 天', webSearchAdvanced: '方案标识与返回数量', webSearchDateFilter: '原文日期核对', webSearchInRange: '范围内', webSearchOutOfRange: '范围外', webSearchUnknown: '日期待核对', webSearchConflict: '日期冲突', webSearchTrace: '查看检索线索', webSearchDateHint: '搜索日期线索（非原文发布日期）', webSearchSummary: '搜索摘要', webSearchOriginalDate: '原文发布', webSearchSourceNote: '填写你的 SearXNG 服务地址，实例需启用 JSON 输出。', webSearchSearxng: 'SearXNG 服务地址',
      sourceCounts: '命中 {hits} 次 · 材料 {materials} 份 · 版本 {versions} 个 · 文章 {articles} 篇', sourceDuplicates: '另有 {n} 处出现 · 查看时间线', sourceTimelineHide: '收起时间线', sourceTimeline: '出现时间线（最新在前）', sourceTimelineCounts: '{hits} 次命中 · {materials} 份材料 · {versions} 个版本', sourceTimelineNote: '按 DOI、站内文章编号或去除跟踪参数的链接认定同一篇文章，不比较标题；多次出现不代表多个独立来源。', sourceTimelineHit: '检索命中 · {plan} · “{query}” 第 {rank} 位', sourceTimelineVersion: '来源采集 · {source}', sourceTimelineFirstSeen: '检索收录 · {plan}', sourceTimelineLoading: '正在读取时间线…', sourceTimelineFailed: '时间线读取失败。',
      sources: '来源与材料', sourcesNote: '按主题检索全网报道，也可持续采集指定网站；在材料箱查看新增内容。',
      sourceCatalog: '来源管理', sourceHistory: '采集记录', sourceOverview: '全库概况', sourceTotal: '全部来源', sourceActive: '已启用', sourceInactive: '已停用', sourceFailed: '采集失败', sourceNoEntries: '未读到材料', sourceSearch: '搜索名称、地址或 ID', sourceWebsite: '网站', sourceStatus: '状态', sourceApply: '筛选', sourceReset: '清除筛选', sourceMatches: '符合条件 {n} 个来源 · 每页最多 50 个', sourceNoMatches: '没有符合筛选条件的来源。', sourceFacetHint: '网站与主题精确匹配；最多显示 20 条匹配提示，也可直接输入。', sourceConfigure: '配置', sourceViewMaterials: '查看材料', sourceCount: '材料数', sourceActions: '操作', sourceSelectPage: '全选当前页', sourceSelectOne: '选择 {name}', sourceSelected: '本页已选 {n} 个', sourceEnableBatch: '启用所选', sourceDisableBatch: '停用所选', sourceClearSelection: '清除选择', sourceBatchSaved: '已更新 {n} 个来源。', sourceBatchConflict: '所选来源已变化或采集正在运行，整批未修改。选择已保留；刷新后清除并重新选择，再提交。', sourceTools: '添加、发现与导入', sourceCloseEditor: '收起配置', sourceAllMaterials: '全部材料', sourceMaterialContext: '当前来源：{name}', sourceLatestJobs: '最近 20 条采集记录', sourceSuccess: '采集成功',
      sourceNew: '添加来源', sourceChoose: '选择来源查看配置，或添加新来源。', sourceEmpty: '尚未配置来源。', sourceName: '来源名称', sourceId: '来源 ID', sourceUrl: '来源地址', sourceKind: '来源类型', sourceRss: 'RSS / Atom 订阅', sourcePage: '网页栏目', sourcePattern: '链接包含文字（可选）', sourcePatternHint: '按链接地址包含的文字过滤，例如 /news/；不填写则使用栏目内可识别链接。', sourceMax: '每次最多条数', sourceEnabled: '启用来源', sourceManual: '手动配置', sourceDiscovered: '自动发现', sourceSave: '保存来源', sourceSaved: '来源配置已保存。', sourceTrial: '试采集', sourceChecked: '最近检查', sourceNever: '尚未检查', sourceItems: '已收录 {n} 条', sourceReadFailed: '读取失败，请刷新重试。', sourceLeave: '有未保存的来源或检索设置。确定放弃并离开？', sourceUnsaved: '有未保存的修改', sourceConflict: '来源已变化或有采集任务正在运行。草稿已保留；任务结束后重新加载最新配置再保存。', sourceInvalid: '请填写有效的 ID、名称、主题、HTTP(S) 地址，条数为 1–200 的整数。',
      sourceDiscover: '发现网站来源', sourceDiscoverNote: '在指定网站内寻找栏目和订阅并试读，已有来源的启用状态保持不变。', sourceSite: '指定网站地址', sourceAuto: '试读成功后自动启用新来源', sourceDiscoverStart: '发现并试读', sourceDiscoverySaved: '任务已提交，可在采集记录中查看结果。', sourceJobs: '最近采集任务', sourceNoJobs: '暂无采集任务。', sourcePartial: '部分完成', sourceJobUnknown: '提交结果待确认。请刷新任务记录后再决定是否重试；不会自动重新提交。', sourceJobReadFailed: '暂时无法读取任务状态；已停止自动刷新，请手动刷新。', sourcePending: '任务运行期间暂停保存与导入配置。', sourceCandidate: '待启用候选', sourceExisting: '已有来源', sourceEntries: '{n} 条线索', sourceTruncated: '已达到本次采集上限，仍可能有更多材料。', sourceStats: '新增 {new} · 更新 {updated} · 未变化 {unchanged}',
      sourceInbox: '材料收件箱', sourceInboxNote: '摘要与原文链接，按首次收录时间排序。发布日期单独记录；重复检查不会把旧材料置顶。', sourceNoMaterials: '当前来源尚无收录材料。', sourcePublished: '发布', sourceDateUnknown: '发布日期未知', sourceFirstSeen: '首次收录', sourceUpdated: '来源更新', sourceVersions: '{n} 个版本', sourceFilter: '按来源查看', sourceRefresh: '刷新', sourcePrev: '上一页', sourceNext: '下一页', sourceRange: '{from}–{to} / {total} 条', sourceImportExport: '导入与导出配置', sourceImportNote: 'JSON 格式：{"sources":[来源配置]}。仅添加新 ID；发生冲突时整批不导入。', sourceJson: '配置 JSON', sourceImport: '导入配置', sourceExport: '导出 JSON', sourceImported: '已导入 {n} 个来源。', sourceBadJson: '请输入包含 sources 数组的 JSON 对象。',
      tasks: "任务中心",
      tasksNote: "提交后自动排队。任务和历史会保留，关闭页面不会停止任务。",
      taskActiveEmpty: "暂无排队或运行中的任务。",
      taskHistoryEmpty: "暂无历史任务。",
      taskQueued: "排队中",
      taskRunning: "运行中",
      taskSucceeded: "已完成",
      taskFailed: "失败",
      taskInterrupted: "运行被中断",
      taskCancelled: "已取消",
      taskPosition: "排队位置：{n}",
      taskCancel: "取消排队",
      taskCancelConfirm: "取消这个尚未开始的任务？",
      taskRefresh: "刷新任务",
      taskOwner: "用户",
      taskCreated: "提交时间",
      taskActive: "活动任务",
      taskHistory: "历史任务",
      taskNew: "生成另一主题的报告",
      taskUnknown: "提交结果待确认。可重新检查，或点击生成重试同一请求；系统不会自动重新提交。",
      users: "账户管理",
      usersNote: "由管理员创建账户。重设密码或停用账户会使其登录失效。",
      username: "用户名",
      password: "密码",
      login: "登录",
      logout: "退出登录",
      loginNote: "请使用管理员分配的账户登录。",
      loginFailed: "登录失败，请检查账户和密码后重试。",
      sessionExpired: "登录已失效，请重新登录。",
      sessionUnavailable: "暂时无法连接服务，请重试。",
      userCreate: "创建账户",
      userCreated: "账户已创建。",
      userUpdated: "账户已更新。",
      userDeactivate: "停用",
      userActivate: "启用",
      userReset: "重设密码",
      userPassword: "新密码（至少 12 个字符）",
      userActive: "启用中",
      userInactive: "已停用",
      userAdmin: "管理员",
      userRegular: "普通用户",
      userDisableConfirm: "停用这个账户并使其登录失效？",
      userPasswordConfirm: "重设密码并使这个账户的现有登录失效？",
      actionFailed: "操作未完成，请重试。",
      modelManaged: "模型由管理员配置。",
      modelContact: "模型尚未就绪，请联系管理员。",
      topicOwnNote: "管理自己的论文主题、关键词与筛选规则。",
      topicBatchNote: "不加入默认批量任务时，仍可在此页面生成报告。",
      guideNoData: '当前时间范围内没有可用于报告的论文。请在主题设置中检查来源、关键词和筛选规则后重试。', guideReviewTriage: '查看论文与排除原因', guideAgain: '生成另一份报告',
      mailTransport: '发信方式', smtp: '通用 SMTP', zeptomail_api: 'ZeptoMail API', mailHost: 'SMTP 服务器', mailPort: 'SMTP 端口（留空按加密方式选择）', mailSecurity: '连接加密', tls: 'TLS（通常为 465）', starttls: 'STARTTLS（通常为 587）', mailUser: 'SMTP 用户名（可选）', mailPassword: 'SMTP 密码 / 授权码', mailSmtpNote: '使用邮箱服务商提供的 SMTP 地址和授权码；无需登录的中继可同时留空用户名和密码。', mailEndpoint: 'ZeptoMail HTTPS 发信地址', mailToken: 'ZeptoMail Send Mail Token', mailSenderName: '发件人显示名称（可选）', mailSettings: '报告邮件', mailDefault: '默认收件人（可选）', mailNote: '默认收件人可留空；发送时修改只对本次生效。发件人与凭据在服务设置中配置；保存后生效。',
      mailOpen: '发送邮件', mailTest: '发送测试邮件', mailSend: '确认发送', mailSending: '正在提交…', mailCancel: '关闭邮件表单', mailRecipients: '本次收件人', mailRecipientsHint: '最多 20 个邮箱，用逗号、分号或空格分隔。', mailSubject: '邮件标题', mailPeriod: '期号', mailSender: '发件人', mailConfirm: '确认提交以下邮件？', mailRequired: '请填写至少一个收件人。',
      mailAccepted: '服务商已接受，尚未确认到达收件箱。', mailFailed: '发送失败。', mailUnknown: '结果待确认。请先检查服务商日志与收件箱，再决定是否重新发送；系统不会自动重试。', mailRequest: '请求编号',
      radar: '科研雷达', overview: '总览', reports: '周报', triage: '筛选', review: 'Memory 审核', runs: '运行与 LLM',
      reviewSoon: '审核页随 memory 合并一起上线', topics: '主题', onPage: '本页', customize: '外观', help: '帮助', online: '已配置', off: '未配置',
      ovTitle: '本周雷达', topicsN: '个主题', kept: '入选', of: '/', collected: '采集', window: '窗口',
      signal: '信号图', signalNote: '离中心越远 = 本窗口入选越多', healthy: '配置有效', failed: '失败', drafts: '份草稿', inactive: '停用',
      draftWaiting: 'memory 草稿待审', lastFailed: '最近一次运行失败',
      pipeline: '流水线', logScale: '条长按对数', fCollected: '采集', fGates: '过规则门', fKept: '判定入选', fFull: '全文',
      awaiting: '待你处理', draftsN: '份草稿', draftsNote: 'memory 提议，等待审核', llmWeek: '近 7 天 LLM', calls: '次调用',
      lastRun: '最近运行', none: '无', topicsNote: '柱 = 近 7 天每天采集条数', keptOf: '入选 / 采集', last: '最近一期', noRuns: '尚无运行', runDetails: '查看逐篇详情', runPaperFailure: '论文处理失败', runFigureFailure: '配图问题', runAbstractFallback: '仅使用摘要',
      latestReports: '最新周报', recentRuns: '最近运行', ledger: '运行记录 →', papers: '篇',
      noReports: '这个主题还没有报告。', topic: '主题', download: '下载 .md', issues: '各期', file: '文件', generated: '生成于',
      firstReport: '生成第一份报告', firstModel: '在服务设置中选择要使用的模型并完成配置。保存只校验配置，不验证连接。', firstSchedule: '报告周期决定内容的时间范围。保存或启用主题不会自动生成报告。', firstChoose: '先创建或选择一个主题。', firstArticles: '此主题只有文章来源；目前仅支持生成 arXiv 论文报告。请在主题设置中添加 arXiv 来源。',
      guideOpen: '开始生成报告', guideIntro: '检查模型、确认主题，再生成报告。', guideClose: '关闭生成向导', guideNext: '下一步', guideBack: '上一步', guideStep: '第 {n} 步，共 3 步', guideModel: '确认模型', guideTopic: '确认主题与来源', guideGenerate: '生成报告', guideSourceReady: '已配置 arXiv 分类和关键词。实际采集结果将在运行后确认。', guideSourceInvalid: '主题配置不完整，请补齐 arXiv 分类和关键词后保存。', guideSavedOnly: '生成使用已保存的设置。请先保存或放弃页面中的草稿。', guideStartNote: '点击后将采集论文、筛选、补充全文并生成报告。会使用已保存的模型设置。', guideRunning: '正在为「{topic}」生成报告', guideBackground: '关闭此窗口不会取消任务。重新打开可查看进度。', guideComplete: '报告已生成。', guideFailed: '未能生成报告。请检查主题分类、关键词和模型设置，再重新尝试。', guideInterrupted: '上次任务已中断或无法找到。请先查看已有报告，再决定是否重新生成。', guideUnavailable: '暂时无法获取任务状态。请重新检查；系统不会自动重新生成。', guideLoadFailed: '暂时无法检查已保存的配置，请重试。', guideStarting: '正在提交生成任务…', guideStartFailed: '无法启动任务，请检查设置后重试。', guideCheck: '重新检查', guideOpenReport: '打开报告', guideViewReports: '查看已有报告', guideWaiting: '等待开始', guideDone: '已完成', guideCurrent: '进行中', guideStopped: '已停止', guideCollect: '采集论文', guideTriage: '筛选论文', guideEnrich: '补充全文', guideDigest: '生成报告',
      llmReady: '模型配置完整；连接未验证', llmIncomplete: '模型配置不完整；请在服务设置中补齐', llmUnsaved: '有未保存修改；保存后检查配置，连接仍需实际运行验证', noAuth: '本地免鉴权（仅 OpenAI compatible）', authRequired: '使用 API Key', authNone: '不发送鉴权信息', noAuthNote: '免鉴权需明确选择，并填写 localhost、回环或私有 IP 地址。不会发送 Authorization，即使已有旧密钥。公网服务仍需 Key。', providerDefaultsNote: '切换 Provider 时，仅默认来源且未编辑的地址和模型随之更新。文件、环境变量和手动输入的值保留；清空普通字段可恢复当前 Provider 默认值。',
      triageEyebrow: '筛选 · 每条为何入选或剔除', stCollected: '采集', stGates: '过规则门', stKept: '判定入选', stFull: '全文',
      stCollectedNote: '本窗口内的条目', stGatesNote: '规则门剔除 {n} 条，不花 LLM', stKeptNote: '{core} 核心 · {sec} 次级 · 判定剔除 {jd}',
      stFullNote: '{n} 篇已有全文', all: '全部', fKeptChip: '入选', judgeDropped: '判定剔除', gateDropped: '规则剔除', untriaged: '未筛选',
      verdict: '结论', item: '条目', reason: '理由', judgeSays: '判定理由', open: '打开原文 ↗', notReached: '未到达', noHit: '未命中',
      hit: '命中领域词', noTerm: '标题无 require_any 词', notJudged: '尚未判定（判定失败或被跳过）', keptCore: '入选 · 核心',
      keptSec: '入选 · 次级', keptUnjudged: '入选 · 待判定', noItems: '这个窗口里没有条目。', fulltextYes: '有全文', via: '来自',
      runsEyebrow: '运行记录 · 每个任务一行，每次 LLM 调用可回放', lanesTitle: '近 8 天', legendJobs: '采集 / 筛选 / 全文', legendDigest: '周报',
      tokensBy: '近 7 天各用途 token', latestCalls: '最近的 LLM 调用', noCalls: '还没有 LLM 调用。', moreChars: '字符未显示',
      mode: '模式', light: '浅色', dark: '深色', device: '跟随系统', color: '颜色', customHue: '自定义色相', surface: '阅读底色',
      paper: '纸张', sepia: '米黄', night: '夜间', surfaceNote: '周报纸面有自己的底色，长文在两种模式下都好读。', reset: '恢复默认',
      customTitle: '自定义 ISBE', customNote: '保存在本浏览器 · 对所有页面生效', close: '关闭外观面板', custom: '自定义', loading: '加载中…',
      shelf: '书架', shelfEyebrow: '报告 · 每一期都在这里', issuesN: '期', pdf: '导出 PDF', weekly: '周报', daily: '日报', monthly: '月报', research: '专题研究', materials: '份材料',
      settings: '服务设置', settingsNote: '配置模型、论文来源与全文解析服务。保存后用于后续任务。',
      llmSettings: '语言模型', llmSettingsNote: '选择服务商，填写对应密钥。其他服务商的密钥可以保留。', provider: '服务商',
      baseUrl: '模型 API 地址', fastModel: '快速模型', smartModel: '深度模型', advanced: '高级模型选项', maxTokens: '最大输出 token', timeout: '超时（秒）', extraBody: '额外请求参数（JSON 对象）',
      fulltextSettings: '全文提取', extractor: '提取方式', extractorLlm: 'LLM 提取（arXiv HTML）', extractorMetrail: 'metrail PDF 服务', extractorOff: '关闭全文获取',
      extractorLlmNote: '获取可用的 arXiv HTML，复用上方服务商、快速模型和 API Key，提取方法、数据与结果，并核验原文引文。HTML 不可用时使用摘要，不进行 PDF OCR 或图片内容识别。',
      extractorLlmFigures: '自动保存有图注的原文配图供报告展示，优先方法、架构与流程图，其次概览或首张有图注的图；组图保留多个面板，不增加视觉模型调用。没有合适配图或下载失败时仍生成文字报告；已有 HTML 缓存可在下次补充全文时补图。',
      extractorMetrailNote: '调用 metrail PDF 服务提取全文，后续分析使用上方配置的模型。', extractorOffNote: '不获取新全文，也不创建或刷新事实卡片；保留已有全文和卡片缓存。未获取全文的论文使用摘要。', metrailRequired: '此模式需要服务地址，也可在主题的 metrail.base_url 中单独配置。',
      extractorDefaultNote: '尚未固定全局方式：每个主题若启用了 metrail 且配置了地址，就沿用 metrail，否则使用 LLM。手动切换后保存可固定全局选择。',
      sourceSettings: '内容来源与联网检索', sourceSettingsNote: '默认直接访问公开 arXiv API。可选接入 essaies 自托管文献库，GitHub Token 也可留空。', sourceAdvanced: '高级来源选项',
      essaiesUrl: 'essaies 地址（可选）', metrailUrl: 'metrail 地址', arxivUrl: 'arXiv API 地址', githubUrl: 'GitHub API 地址', githubToken: 'GitHub Token（可选）',
      sourceEnvironment: '环境变量', sourceFile: '本地设置', sourceDefault: '默认值', environmentLock: '由环境变量提供，请在运行环境中修改。',
      keepSecret: '留空保留已有密钥', newSecret: '输入密钥', removeSecret: '移除已保存密钥', undoRemove: '撤销移除', removingSecret: '保存后移除密钥',
      settingsStorage: '密钥仅保存在服务器本地，不会回传到页面。环境变量优先于本页设置；普通字段留空可恢复默认值。',
      saveSettings: '保存设置', savingSettings: '保存中…', savedSettings: '设置已保存。请查看上方模型配置状态；尚未验证连接。', unsavedSettings: '有未保存的修改', discardSettings: '放弃修改',
      leaveSettings: '有未保存的服务设置。确定放弃这些修改并离开？', invalidExtraBody: '额外请求参数必须是 JSON 对象。', settingsUnavailable: '无法加载服务设置。', retry: '重试',
      topicSettings: '主题设置', topicSettingsNote: '管理关注的内容、采集来源和筛选规则。模型与服务地址在服务设置中配置。',
      topicNew: '新建主题', topicSearch: '搜索名称或 ID', topicChoose: '选择一个主题查看和编辑，或新建主题。', topicEmpty: '还没有主题。创建第一个主题后即可采集。', topicNoMatch: '没有匹配的主题。',
      topicBasic: '基本信息', topicId: '主题 ID', topicLabel: '主题名称', topicLang: '报告语言', topicCadence: '报告周期', topicWindow: '回看天数', topicMaxPapers: '每期最多论文数', topicActive: '加入默认批量任务', topicActiveNote: '停用后仍可用命令指定此主题运行。',
      topicSources: '采集来源', topicArxiv: '采集 arXiv 论文', topicCategories: 'arXiv 分类（必填）', topicKeywords: '论文关键词（必填）', topicMaxResults: '每次最多采集数', topicRepos: 'GitHub 仓库（可选）', topicLines: '每行一项。', topicRepoNote: '每行一个 owner/name。',
      topicCategoryHelp: '启用 arXiv 时至少一项，每行一个代码。例如 cs.LG（机器学习）、cs.AI（人工智能）、cs.RO（机器人）。', topicKeywordHelp: '启用 arXiv 时至少一项，每行一个词或短语，例如 robot learning。', topicQueryHelp: '分类之间为“或”，关键词之间为“或”；两组之间为“且”。论文须命中至少一个分类，且标题或摘要命中至少一个关键词/短语。', topicTaxonomy: '查看 arXiv 官方分类说明', topicNeedCategories: '请至少填写一个 arXiv 分类。', topicNeedKeywords: '请至少填写一个论文关键词。',
      topicFeeds: 'RSS 订阅（可选）', topicFeedName: '订阅名称', topicFeedUrl: '订阅 URL', topicAddFeed: '添加 RSS', topicRemoveFeed: '移除第 {n} 个 RSS', topicFeedNote: 'URL 支持 ${VAR} 环境变量模板，保存时保留原文。',
      topicRules: '筛选规则（可选）', topicIntent: '检索意图', topicIntentNote: '填写筛选规则时需要说明关注目标。', topicInScope: '关注范围', topicOutScope: '排除范围', topicExcluded: '排除关键词', topicRequired: '必须包含的关键词', topicQuality: '质量标准',
      topicSave: '保存主题', topicSaving: '正在保存…', topicSaved: '主题已保存。', topicDeleted: '主题配置已删除，历史资料已保留。', topicDelete: '删除主题', topicReload: '重新加载', topicUnsaved: '有未保存的主题修改',
      topicLeave: '有未保存的主题修改。确定放弃修改并继续？', topicDeleteConfirm: '删除主题“{id}”的配置？历史报告、条目和记忆会保留，重用相同 ID 会接续历史。当前草稿也会放弃。',
      topicConflict: '主题已被其他操作修改。你的草稿仍保留；请重新加载最新版本后再编辑。', topicInvalid: '配置存在错误', topicUnreadable: '该配置无法解析为表单。请修复原始配置文件，或删除此配置后重新创建；历史资料会保留。', topicAdvancedKept: '计划、解析器及其他未显示的高级配置会原样保留。',
      topicBadId: '主题 ID 请使用小写字母、数字、横线或下划线，并以字母或数字开头。', topicNeedLabel: '请填写主题名称。', topicNeedSource: '请启用 arXiv 或添加 RSS 订阅。', topicNeedArxiv: 'arXiv 分类和论文关键词至少各填一项。', topicNeedIntent: '请为筛选规则填写检索意图。', topicBadNumber: '天数、采集数和评论文数必须是正整数。', topicBadFeed: '每个 RSS 都需要名称和 URL。', topicBadRepo: 'GitHub 仓库请按 owner/name 填写。', topicMissing: '所选主题已不存在。',
    },
    en: {
      articleStoredUnavailable: 'The saved original cannot be read right now. Please try again later.', articleExpand: 'Read original text', articleCollapse: 'Collapse original text', articleLoading: 'Reading the original article…', articleUnavailable: 'The source website did not respond. Open the article title or try again later.', articleUnsupported: 'This website’s article layout is not supported yet. Open the title to read the original.', articleReadAt: 'Original webpage text · Read {time}', articleSavedAt: 'Saved original text · Retrieved {time}',
      mediaWeekly: 'Compile weekly materials', mediaWeeklyTopic: 'Report topic', mediaWeeklyOwnTopic: '{name} (this search plan)', mediaWeeklyEnd: 'Week ending (Sunday)', mediaWeeklyWindow: 'This issue: {start} to {end}', mediaWeeklyNote: 'Compile stored search materials by date for preparing an industry briefing. Content remains search snippets, not a thematic briefing. Uncertain and conflicting dates go in the appendix.', mediaWeeklyInvalid: 'Choose a report topic and a completed Sunday before today.', mediaWeeklyCreated: 'Material compilation created.', mediaWeeklyOpen: 'Open material compilation', mediaWeeklyCounts: 'This week {weekly} · Earlier background {background} · After week {newer} · Uncertain dates {unknown} · Conflicting dates {conflict} · Other dates {outside}', mediaWeeklyClose: 'Close compilation settings',
      webSearchRetryBatch: 'Retry this batch',
      webSearchRecheck: 'Recheck dates', webSearchContinue: 'Continue checking {n} remaining', webSearchRecheckStats: 'Checked {checked} this batch · {remaining} remaining', webSearchPlanContext: 'Current plan: {name}', webSearchPlanWindow: 'Plan window: {start} to {end}', webSearchRetainedDate: 'The latest date check could not finish; the previous record is retained.',
      webSearchRegion: 'Region (optional)', webSearchInstitution: 'Institution (optional)', webSearchRegionHint: 'Region and institution are added to every query as quoted phrases. The search service cannot filter on them, so check them in the results.', webSearchSyntax: 'Syntax as in Google Scholar: spaces mean all words; uppercase OR means either; "phrase" matches exactly; -word excludes; site:domain; intitle:word. Example: Shanghai "green finance" ESG OR sustainability -jobs. author: and source: are not supported.', webSearchUnexecuted: 'Not applied by the search service; check in the results: {items}', webSearchListSep: ', ', webSearchC_terms: 'Search words', webSearchC_phrase: 'Exact phrase', webSearchC_boolean: 'OR groups', webSearchC_exclusion: 'Excluded words', webSearchC_site: 'Site filter (site:)', webSearchC_title: 'Title filter (intitle:)', webSearchC_institution: 'Institution', webSearchC_region: 'Region', webSearchC_language: 'Language', webSearchC_publication_window: 'Publication window',
      articleDns: 'Original-page address resolution', articleDnsSystem: 'System default', articleDnsCloudflare: 'Cloudflare encrypted DNS', articleDnsNote: 'Used to read original articles and publication dates. Cloudflare receives public domain names and can resolve failures caused by virtual IP addresses.',
      webSearchNoDateMatches: 'No material matches the current date filter.',
      webSearchWindow: 'Search date window', webSearchDateRead: 'Original date information checked', webSearchDateNotRead: 'Original date not checked yet', webSearchDateReadFailed: 'Original page could not be read', webSearchLimited: 'Only the first batch of results was collected; more reporting may be available.', webSearchEnginesUnavailable: '{n} search engines did not respond.', webSearchSearxngWindow: 'When available, SearXNG receives a month/year hint that upstream engines may ignore. Original dates are checked against your exact range for up to 20 pages per run; all other results are retained.', webSearchBaiduRemoved: 'Baidu (removed)', webSearchBaiduRemovedHint: 'Baidu search has been removed. Switch this plan to SearXNG and save it to search again; its materials can still be viewed and rechecked.',
      webSearch: 'Web discovery', webSearchNote: 'Find reporting across media and public websites by topic, then check original dates in the material inbox.', webSearchPlans: 'Search plans', webSearchNew: 'New plan', webSearchEmpty: 'No saved plans yet. Start with the example form.', webSearchName: 'Plan name', webSearchId: 'Plan ID', webSearchProvider: 'Search service', webSearchQueries: 'Search queries (one per line)', webSearchQueriesHint: 'Up to 8 queries, 200 characters each. Each runs separately. Search syntax, including site: filters, depends on the provider.', webSearchStart: 'Start date', webSearchEnd: 'End date', webSearchLanguage: 'Search language', webSearchMax: 'Maximum results per query', webSearchSave: 'Save plan', webSearchRun: 'Save & search', webSearchRunSaved: 'Run search', webSearchSaved: 'Search plan saved.', webSearchInvalid: 'Enter an ID, name, topic, 1–8 queries and a valid date range. Queries allow up to 200 characters; results must be 1–20.', webSearchUnavailable: 'This search service is not configured. You can save a plan and configure the service before running it.', webSearchSettings: 'Configure search service', webSearchReady: 'Service configured; the connection will be checked during search.', webSearchWindowNote: 'Request a date window, then check original publication dates. Unknown, out-of-range and conflicting dates remain available for review.', webSearchManual: 'Runs on demand; saving a plan does not schedule it. Search and date checks do not use an LLM.', webSearchRecent7: 'Last 7 days', webSearchRecent30: 'Last 30 days', webSearchAdvanced: 'Plan ID & result count', webSearchDateFilter: 'Original date check', webSearchInRange: 'In range', webSearchOutOfRange: 'Out of range', webSearchUnknown: 'Date unverified', webSearchConflict: 'Conflicting dates', webSearchTrace: 'Search provenance', webSearchDateHint: 'Search date hint (not original publication)', webSearchSummary: 'Search excerpt', webSearchOriginalDate: 'Original publication', webSearchSourceNote: 'Use your own SearXNG URL; the instance must enable JSON output.', webSearchSearxng: 'SearXNG URL',
      sourceCounts: '{hits} hits · {materials} materials · {versions} versions · {articles} articles', sourceDuplicates: 'Seen {n} more times · timeline', sourceTimelineHide: 'Hide timeline', sourceTimeline: 'Where this article appeared (newest first)', sourceTimelineCounts: '{hits} hits · {materials} materials · {versions} versions', sourceTimelineNote: 'The same article is matched by DOI, the site’s own article ID or the link without tracking parameters, never by title. Several appearances are not several independent sources.', sourceTimelineHit: 'Search hit · {plan} · “{query}” rank {rank}', sourceTimelineVersion: 'Source collection · {source}', sourceTimelineFirstSeen: 'Found by search · {plan}', sourceTimelineLoading: 'Loading timeline…', sourceTimelineFailed: 'Could not load the timeline.',
      sources: 'Sources & materials', sourcesNote: 'Search the web by topic or collect from selected websites, then review new material in the inbox.',
      sourceCatalog: 'Source management', sourceHistory: 'Collection history', sourceOverview: 'Whole catalog', sourceTotal: 'All sources', sourceActive: 'Enabled', sourceInactive: 'Disabled', sourceFailed: 'Collection failed', sourceNoEntries: 'No material found', sourceSearch: 'Search names, URLs, or IDs', sourceWebsite: 'Website', sourceStatus: 'Status', sourceApply: 'Apply filters', sourceReset: 'Clear filters', sourceMatches: '{n} matching sources · Up to 50 per page', sourceNoMatches: 'No sources match these filters.', sourceFacetHint: 'Website and topic match exactly. Up to 20 matching suggestions are shown; you can also type a value.', sourceConfigure: 'Configure', sourceViewMaterials: 'View material', sourceCount: 'Materials', sourceActions: 'Actions', sourceSelectPage: 'Select current page', sourceSelectOne: 'Select {name}', sourceSelected: '{n} selected on this page', sourceEnableBatch: 'Enable selected', sourceDisableBatch: 'Disable selected', sourceClearSelection: 'Clear selection', sourceBatchSaved: '{n} sources updated.', sourceBatchConflict: 'Selected sources changed or collection is running. Nothing was changed. Selection is kept; refresh, clear and reselect before submitting again.', sourceTools: 'Add, discover & import', sourceCloseEditor: 'Close configuration', sourceAllMaterials: 'All material', sourceMaterialContext: 'Current source: {name}', sourceLatestJobs: 'Latest 20 collection records', sourceSuccess: 'Collection succeeded',
      sourceNew: 'Add source', sourceChoose: 'Select a source to edit, or add a source.', sourceEmpty: 'No configured sources yet.', sourceName: 'Source name', sourceId: 'Source ID', sourceUrl: 'Source URL', sourceKind: 'Source type', sourceRss: 'RSS / Atom feed', sourcePage: 'Webpage listing', sourcePattern: 'Link contains (optional)', sourcePatternHint: 'Filter link URLs by included text, such as /news/. Leave empty to use identifiable links in the listing.', sourceMax: 'Maximum items per collection', sourceEnabled: 'Enable source', sourceManual: 'Manually configured', sourceDiscovered: 'Discovered', sourceSave: 'Save source', sourceSaved: 'Source configuration saved.', sourceTrial: 'Try collection', sourceChecked: 'Last checked', sourceNever: 'Not checked yet', sourceItems: '{n} items saved', sourceReadFailed: 'Could not load this section. Refresh to try again.', sourceLeave: 'Discard unsaved source or search settings and leave?', sourceUnsaved: 'Unsaved changes', sourceConflict: 'The source changed or a collection job is running. Your draft is kept; reload the latest configuration after the job finishes.', sourceInvalid: 'Enter a valid ID, name, topic, HTTP(S) URL, and an integer item limit from 1 to 200.',
      sourceDiscover: 'Discover website sources', sourceDiscoverNote: 'Find listings and feeds within this website and try reading them. Existing sources keep their enabled state.', sourceSite: 'Website URL', sourceAuto: 'Automatically enable new sources after a successful trial', sourceDiscoverStart: 'Discover and try', sourceDiscoverySaved: 'Task submitted. Open Collection history for results.', sourceJobs: 'Recent collection tasks', sourceNoJobs: 'No collection tasks yet.', sourcePartial: 'Partly completed', sourceJobUnknown: 'Submission is unconfirmed. Refresh task history before retrying; it will not be resubmitted automatically.', sourceJobReadFailed: 'Task status is unavailable. Automatic refresh has stopped; refresh manually to check again.', sourcePending: 'Configuration saves and imports are paused while collection tasks run.', sourceCandidate: 'Disabled candidate', sourceExisting: 'Existing source', sourceEntries: '{n} items', sourceTruncated: 'This collection reached its limit; more material may remain.', sourceStats: 'New {new} · Updated {updated} · Unchanged {unchanged}',
      sourceInbox: 'Material inbox', sourceInboxNote: 'Excerpts and original links, ordered by first collection. Publication dates are shown separately; repeated checks do not move old items to the top.', sourceNoMaterials: 'No collected material for this selection yet.', sourcePublished: 'Published', sourceDateUnknown: 'Publication date unknown', sourceFirstSeen: 'First collected', sourceUpdated: 'Source updated', sourceVersions: '{n} versions', sourceFilter: 'Filter by source', sourceRefresh: 'Refresh', sourcePrev: 'Previous', sourceNext: 'Next', sourceRange: '{from}–{to} / {total} items', sourceImportExport: 'Import and export configuration', sourceImportNote: 'JSON format: {"sources":[source configurations]}. Adds new IDs only; any conflict rejects the whole import.', sourceJson: 'Configuration JSON', sourceImport: 'Import configuration', sourceExport: 'Export JSON', sourceImported: '{n} sources imported.', sourceBadJson: 'Enter a JSON object containing a sources array.',
      tasks: "Task center",
      tasksNote: "Submitted tasks enter the queue automatically. Tasks and history remain after you close this page.",
      taskActiveEmpty: "No queued or running tasks.",
      taskHistoryEmpty: "No task history yet.",
      taskQueued: "Queued",
      taskRunning: "Running",
      taskSucceeded: "Completed",
      taskFailed: "Failed",
      taskInterrupted: "Interrupted",
      taskCancelled: "Cancelled",
      taskPosition: "Queue position: {n}",
      taskCancel: "Cancel queued task",
      taskCancelConfirm: "Cancel this task before it starts?",
      taskRefresh: "Refresh tasks",
      taskOwner: "Owner",
      taskCreated: "Submitted",
      taskActive: "Active tasks",
      taskHistory: "Task history",
      taskNew: "Generate for another topic",
      taskUnknown: "Submission is unconfirmed. Check again, or click Generate to retry the same request. It will not be resubmitted automatically.",
      users: "Accounts",
      usersNote: "Administrators create accounts. Password resets and deactivation end existing sessions.",
      username: "Username",
      password: "Password",
      login: "Sign in",
      logout: "Sign out",
      loginNote: "Sign in with the account provided by your administrator.",
      loginFailed: "Sign-in failed. Check your account and password, then try again.",
      sessionExpired: "Your session expired. Please sign in again.",
      sessionUnavailable: "The service is temporarily unavailable. Please try again.",
      userCreate: "Create account",
      userCreated: "Account created.",
      userUpdated: "Account updated.",
      userDeactivate: "Deactivate",
      userActivate: "Activate",
      userReset: "Reset password",
      userPassword: "New password (at least 12 characters)",
      userActive: "Active",
      userInactive: "Inactive",
      userAdmin: "Administrator",
      userRegular: "User",
      userDisableConfirm: "Deactivate this account and end its existing sessions?",
      userPasswordConfirm: "Reset this password and end the account’s existing sessions?",
      actionFailed: "The action could not be completed. Please try again.",
      modelManaged: "Your administrator manages the model configuration.",
      modelContact: "The model is not ready. Please contact your administrator.",
      topicOwnNote: "Manage your paper topics, keywords, and filtering rules.",
      topicBatchNote: "A topic excluded from default batch jobs can still generate reports here.",
      guideNoData: 'No usable papers remain in this topic’s current window. Review its sources, keywords, and filters in Topics before trying again.', guideReviewTriage: 'Review papers and exclusion reasons', guideAgain: 'Generate another report',
      mailTransport: 'Delivery method', smtp: 'SMTP', zeptomail_api: 'ZeptoMail API', mailHost: 'SMTP server', mailPort: 'SMTP port (blank uses TLS mode default)', mailSecurity: 'Connection security', tls: 'TLS (usually 465)', starttls: 'STARTTLS (usually 587)', mailUser: 'SMTP username (optional)', mailPassword: 'SMTP password / app password', mailSmtpNote: 'Use your mail provider’s SMTP host and app password. Leave both username and password empty for an unauthenticated relay.', mailEndpoint: 'ZeptoMail HTTPS endpoint', mailToken: 'ZeptoMail Send Mail Token', mailSenderName: 'Sender display name (optional)', mailSettings: 'Report email', mailDefault: 'Default recipients (optional)', mailNote: 'Defaults may be empty. Recipient edits at send time apply only to that email. Configure the sender and credentials in Service settings, then save.',
      mailOpen: 'Send email', mailTest: 'Send test email', mailSend: 'Confirm send', mailSending: 'Submitting…', mailCancel: 'Close email form', mailRecipients: 'Recipients for this email', mailRecipientsHint: 'Up to 20 addresses, separated by commas, semicolons or spaces.', mailSubject: 'Subject', mailPeriod: 'Period', mailSender: 'Sender', mailConfirm: 'Submit this email?', mailRequired: 'Enter at least one recipient.',
      mailAccepted: 'Accepted by the provider; inbox delivery is not confirmed.', mailFailed: 'Sending failed.', mailUnknown: 'Result unknown. Check the provider log and inbox before sending again; the app will not retry automatically.', mailRequest: 'Request ID',
      radar: 'Research radar', overview: 'Overview', reports: 'Reports', triage: 'Triage', review: 'Memory review', runs: 'Runs & LLM',
      reviewSoon: 'Memory review comes with the memory merge milestone', topics: 'Topics', onPage: 'On this page', customize: 'Customize', help: 'Help',
      online: 'configured', off: 'not set', ovTitle: 'This week on the radar', topicsN: 'topics', kept: 'kept', of: 'of', collected: 'collected',
      window: 'window', signal: 'Signal map', signalNote: 'distance from centre = items kept this window', healthy: 'configuration valid', failed: 'failed',
      drafts: 'drafts', inactive: 'inactive', draftWaiting: 'memory draft waiting', lastFailed: 'last run failed', pipeline: 'Pipeline',
      logScale: 'bar length on a log scale', fCollected: 'Collected', fGates: 'Passed gates', fKept: 'Kept by judge', fFull: 'Full text',
      awaiting: 'Awaiting you', draftsN: 'drafts', draftsNote: 'memory proposals to review', llmWeek: 'LLM, last 7 days', calls: 'calls',
      lastRun: 'Last run', none: 'none', topicsNote: 'bars = items collected per day, last 7 days', keptOf: 'kept of', last: 'last',
      noRuns: 'no runs yet', runDetails: 'View paper details', runPaperFailure: 'Paper processing failed', runFigureFailure: 'Figure issue', runAbstractFallback: 'Abstract only', latestReports: 'Latest reports', recentRuns: 'Recent runs', ledger: 'ledger →', papers: 'papers',
      noReports: 'No reports for this topic yet.', topic: 'Topic', download: 'Download .md', issues: 'Issues',
      firstReport: 'Create the first report', firstModel: 'Choose your models and complete their configuration in Settings. Saving checks configuration, not connectivity.', firstSchedule: 'Report cadence sets the time period. Saving or enabling a topic does not generate a report automatically.', firstChoose: 'Create or select a topic first.', firstArticles: 'This topic has article sources only. Reports currently require arXiv papers; add an arXiv source in Topics.',
      guideOpen: 'Start report guide', guideIntro: 'Check your model, confirm a topic, then generate a report.', guideClose: 'Close report guide', guideNext: 'Next', guideBack: 'Back', guideStep: 'Step {n} of 3', guideModel: 'Check your model', guideTopic: 'Confirm topic and sources', guideGenerate: 'Generate report', guideSourceReady: 'arXiv categories and keywords are configured. Available papers will be checked during collection.', guideSourceInvalid: 'Complete and save the topic’s arXiv categories and keywords.', guideSavedOnly: 'Generation uses saved settings. Save or discard the draft on this page first.', guideStartNote: 'This collects papers, filters them, retrieves full text, and creates a report using saved model settings.', guideRunning: 'Generating a report for “{topic}”', guideBackground: 'Closing this window does not cancel the task. Reopen it to check progress.', guideComplete: 'Your report is ready.', guideFailed: 'A report could not be created. Check the topic’s categories, keywords, and model settings before trying again.', guideInterrupted: 'The previous task was interrupted or could not be found. Check existing reports before generating again.', guideUnavailable: 'Task status is temporarily unavailable. Check again; the app will not restart generation automatically.', guideLoadFailed: 'Saved configuration could not be checked. Try again.', guideStarting: 'Starting generation…', guideStartFailed: 'Generation could not start. Check settings and try again.', guideCheck: 'Check again', guideOpenReport: 'Open report', guideViewReports: 'View existing reports', guideWaiting: 'Waiting', guideDone: 'Complete', guideCurrent: 'In progress', guideStopped: 'Stopped', guideCollect: 'Collect papers', guideTriage: 'Filter papers', guideEnrich: 'Retrieve full text', guideDigest: 'Generate report',
      llmReady: 'Model configuration complete; connection not verified', llmIncomplete: 'Model configuration incomplete; complete it in Settings', llmUnsaved: 'Unsaved changes; save to check configuration, then run a task to verify the connection', noAuth: 'Local no-auth (OpenAI compatible only)', authRequired: 'Use API key', authNone: 'Send no authentication', noAuthNote: 'No-auth must be explicitly selected with a localhost, loopback or private IP URL. Authorization is omitted even if an old key exists. Public services still require a key.', providerDefaultsNote: 'Switching Provider updates only unedited fields whose source is Default. File, environment and manually entered values are kept. Clear a regular field to restore the current Provider default.',
      file: 'File', generated: 'generated', triageEyebrow: 'Triage · why each item was kept or dropped', stCollected: 'Collected',
      stGates: 'Passed gates', stKept: 'Kept by judge', stFull: 'Full text', stCollectedNote: 'items in this window',
      stGatesNote: '{n} dropped by rules · no LLM cost', stKeptNote: '{core} core · {sec} secondary · {jd} judged out',
      stFullNote: '{n} papers with full text', all: 'All', fKeptChip: 'Kept', judgeDropped: 'Judge dropped', gateDropped: 'Gate dropped',
      untriaged: 'Not triaged', verdict: 'Verdict', item: 'Item', reason: 'Reason', judgeSays: 'Judge says', open: 'Open paper ↗',
      notReached: 'not reached', noHit: 'no hit', hit: 'domain term found', noTerm: 'no require_any term in title',
      notJudged: 'not judged yet (judge failed or skipped it)', keptCore: 'kept · core', keptSec: 'kept · secondary',
      keptUnjudged: 'kept · unjudged', noItems: 'No items in this window.', fulltextYes: 'full text', via: 'via',
      runsEyebrow: 'Ledger · every job is one row, every LLM call is replayable', lanesTitle: 'Last 8 days',
      legendJobs: 'collect / triage / enrich', legendDigest: 'digest', tokensBy: 'Tokens by purpose, last 7 days',
      latestCalls: 'Latest LLM calls', noCalls: 'No LLM calls yet.', moreChars: 'more characters not shown', mode: 'Mode',
      light: 'Light', dark: 'Dark', device: 'Device', color: 'Color', customHue: 'Custom hue', surface: 'Reading surface',
      paper: 'Paper', sepia: 'Sepia', night: 'Night', surfaceNote: 'The report sheet keeps its own surface, so long reads stay comfortable in either mode.',
      reset: 'Reset to default', customTitle: 'Customize ISBE', customNote: 'Saved in this browser · applies to every page',
      close: 'Close customize panel', custom: 'Custom', loading: 'Loading…',
      shelf: 'Library', shelfEyebrow: 'Reports · every issue, newest first', issuesN: 'issues', pdf: 'Save as PDF', weekly: 'weekly', daily: 'daily', monthly: 'monthly', research: 'research', materials: 'sources',
      settings: 'Settings', settingsNote: 'Connect models, paper sources, and full-text services. Saved settings apply to future tasks.',
      llmSettings: 'Language model', llmSettingsNote: 'Choose a provider and add its API key. Keys for other providers can stay saved.', provider: 'Provider',
      baseUrl: 'Model API URL', fastModel: 'Fast model', smartModel: 'Smart model', advanced: 'Advanced model options', maxTokens: 'Maximum output tokens', timeout: 'Timeout (seconds)', extraBody: 'Extra request parameters (JSON object)',
      fulltextSettings: 'Full-text extraction', extractor: 'Extraction method', extractorLlm: 'LLM extraction (arXiv HTML)', extractorMetrail: 'metrail PDF service', extractorOff: 'Disable full-text fetching',
      extractorLlmNote: 'Fetch available arXiv HTML and use the provider, fast model, and key above to extract methods, data, and results with verbatim quote checks. Papers without HTML use abstracts. This mode does not run PDF OCR or recognize image content.',
      extractorLlmFigures: 'Save captioned source figures locally for reports, preferring methods, architectures, or workflows, then overviews or the first captioned figure. Keep panels together without extra vision-model calls. Missing or failed images leave a text report; cached HTML papers can gain figures on the next enrichment run.',
      extractorMetrailNote: 'Use the metrail PDF service to extract full text, then analyze it with the model configured above.', extractorOffNote: 'Do not fetch new full text or create or refresh fact cards. Keep existing text and card caches. Papers without full text use abstracts.', metrailRequired: 'This mode needs a service URL, which may also be set in a topic’s metrail.base_url.',
      extractorDefaultNote: 'No global method is fixed yet: each topic uses metrail when enabled with a URL, otherwise LLM. Change the selection and save to fix the global method.',
      sourceSettings: 'Content sources & web search', sourceSettingsNote: 'Use the public arXiv API by default. An essaies self-hosted library and a GitHub token are optional.', sourceAdvanced: 'Advanced source options',
      essaiesUrl: 'essaies URL (optional)', metrailUrl: 'metrail URL', arxivUrl: 'arXiv API URL', githubUrl: 'GitHub API URL', githubToken: 'GitHub token (optional)',
      sourceEnvironment: 'Environment', sourceFile: 'Local settings', sourceDefault: 'Default', environmentLock: 'Set by an environment variable. Edit it in your runtime environment.',
      keepSecret: 'Leave blank to keep the existing key', newSecret: 'Enter a key', removeSecret: 'Remove saved key', undoRemove: 'Undo removal', removingSecret: 'Key will be removed on save',
      settingsStorage: 'Keys are stored locally on the server and never returned to this page. Environment variables take priority; clear a regular field to restore its default.',
      saveSettings: 'Save settings', savingSettings: 'Saving…', savedSettings: 'Settings saved. See the model configuration status above; no connection test was run.', unsavedSettings: 'Unsaved changes', discardSettings: 'Discard changes',
      leaveSettings: 'You have unsaved service settings. Discard these changes and leave?', invalidExtraBody: 'Extra request parameters must be a JSON object.', settingsUnavailable: 'Could not load service settings.', retry: 'Retry',
      topicSettings: 'Topic settings', topicSettingsNote: 'Manage what to follow, where to collect it, and how to filter it. Configure models and service URLs in Settings.',
      topicNew: 'New topic', topicSearch: 'Search name or ID', topicChoose: 'Choose a topic to view and edit, or create a new one.', topicEmpty: 'No topics yet. Create your first topic to start collecting.', topicNoMatch: 'No matching topics.',
      topicBasic: 'Basics', topicId: 'Topic ID', topicLabel: 'Topic name', topicLang: 'Report language', topicCadence: 'Report cadence', topicWindow: 'Lookback days', topicMaxPapers: 'Maximum papers per report', topicActive: 'Include in default batch jobs', topicActiveNote: 'An inactive topic can still run when explicitly named in a command.',
      topicSources: 'Sources', topicArxiv: 'Collect arXiv papers', topicCategories: 'arXiv categories (required)', topicKeywords: 'Paper keywords (required)', topicMaxResults: 'Maximum papers per collection', topicRepos: 'GitHub repositories (optional)', topicLines: 'One entry per line.', topicRepoNote: 'One owner/name per line.',
      topicCategoryHelp: 'At least one when arXiv is enabled; one code per line. Examples: cs.LG (machine learning), cs.AI (artificial intelligence), cs.RO (robotics).', topicKeywordHelp: 'At least one when arXiv is enabled; one word or phrase per line, e.g. robot learning.', topicQueryHelp: 'Categories are combined with OR, keywords with OR, and the two groups with AND. A paper must match a category and have a keyword/phrase in its title or abstract.', topicTaxonomy: 'Browse the official arXiv category taxonomy', topicNeedCategories: 'Add at least one arXiv category.', topicNeedKeywords: 'Add at least one paper keyword.',
      topicFeeds: 'RSS feeds (optional)', topicFeedName: 'Feed name', topicFeedUrl: 'Feed URL', topicAddFeed: 'Add RSS feed', topicRemoveFeed: 'Remove RSS feed {n}', topicFeedNote: 'URLs support ${VAR} environment templates and are saved as entered.',
      topicRules: 'Filtering rules (optional)', topicIntent: 'Research intent', topicIntentNote: 'Describe the goal when adding filtering rules.', topicInScope: 'In scope', topicOutScope: 'Out of scope', topicExcluded: 'Excluded keywords', topicRequired: 'Required keywords', topicQuality: 'Quality bar',
      topicSave: 'Save topic', topicSaving: 'Saving…', topicSaved: 'Topic saved.', topicDeleted: 'Topic configuration deleted. Historical data is retained.', topicDelete: 'Delete topic', topicReload: 'Reload', topicUnsaved: 'Unsaved topic changes',
      topicLeave: 'You have unsaved topic changes. Discard them and continue?', topicDeleteConfirm: 'Delete the configuration for “{id}”? Reports, items, and memory stay available; reusing this ID continues its history. Current edits will also be discarded.',
      topicConflict: 'This topic changed elsewhere. Your draft is preserved; reload the latest version before editing again.', topicInvalid: 'Invalid configuration', topicUnreadable: 'This configuration cannot be shown as a form. Repair the original file, or delete the configuration and create it again. Historical data will remain.', topicAdvancedKept: 'Schedules, parser options, and other advanced fields not shown here are preserved.',
      topicBadId: 'Use lowercase letters, numbers, hyphens, or underscores for the ID, starting with a letter or number.', topicNeedLabel: 'Enter a topic name.', topicNeedSource: 'Enable arXiv or add an RSS feed.', topicNeedArxiv: 'Add at least one arXiv category and one paper keyword.', topicNeedIntent: 'Add a research intent for these filtering rules.', topicBadNumber: 'Days and paper limits must be positive whole numbers.', topicBadFeed: 'Each RSS feed needs a name and URL.', topicBadRepo: 'Use owner/name for each GitHub repository.', topicMissing: 'The selected topic no longer exists.',
    },
  };
  const tr = (k, vars) => {
    let s = (L[prefs.lang] || L.en)[k] ?? L.en[k] ?? k;
    // A function replacement: `$&`, `$'` and the like in a name are text, not patterns.
    for (const [n, v] of Object.entries(vars || {})) s = s.replace(`{${n}}`, () => String(v));
    return s;
  };

  // ---------- formatting ----------
  const locale = () => (prefs.lang === 'zh' ? 'zh-CN' : 'en-US');
  const num = (n) => Number(n || 0).toLocaleString(locale());
  const compact = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n || 0));
  const pad = (n) => String(n).padStart(2, '0');
  const when = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return d.toDateString() === new Date().toDateString() ? hm : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
  };
  const dur = (a, b) => {
    if (!a || !b) return '—';
    const s = Math.max(0, Math.round((new Date(b) - new Date(a)) / 1000));
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${pad(s % 60)}s`;
  };
  const day = (iso) => (iso || '').slice(0, 10);

  // ---------- data ----------
  // One fetch per URL per page view; navigation clears it, local UI changes reuse it.
  // A failed fetch is dropped, so the next render asks again instead of repeating the error.
  const memo = new Map();
  const api = (url) => {
    if (!memo.has(url)) {
      const request = http(url).then(async (r) => {
        if (r.status === 404) return null;
        if (!r.ok) throw new Error(`${url}: ${(await r.json().catch(() => ({}))).error || r.status}`);
        return r.json();
      });
      request.catch(() => { if (memo.get(url) === request) memo.delete(url); });
      memo.set(url, request);
    }
    return memo.get(url);
  };

  let session = null, sessionRequest = null, sessionEpoch = 0, authBusy = false, authError = '';
  const isAdmin = () => session?.user?.role === 'admin';
  const activeTask = (job) => ['queued', 'running'].includes(job?.state);
  async function http(url, options) {
    const epoch = sessionEpoch;
    const response = await fetch(url, options);
    if (epoch !== sessionEpoch) throw new Error(tr('sessionExpired'));
    if (response.status === 401 && url !== '/api/login') {
      expireSession();
      throw new Error(tr('sessionExpired'));
    }
    return { ok: response.ok, status: response.status, json: async () => { const data = await response.json(); if (epoch !== sessionEpoch) throw new Error(tr('sessionExpired')); return data; } };
  }
  function expireSession() {
    ++sessionEpoch; ++seq; ++guide.request; ++tasksRequest; closeGuide(); clearTimeout(tasksTimer);
    memo.clear(); session = { mode: 'shared', user: null, csrf_token: '' }; sessionRequest = null;
    authError = 'sessionExpired'; authBusy = false;
    settingsSnapshot = null; settingsEdits = {}; settingsSaving = false; settingsNotice = ''; settingsError = '';
    topicsSnapshot = null; topicDetail = null; topicOriginal = null; topicEdits = {}; topicSearch = ''; topicBusy = false; topicError = ''; topicNotice = ''; topicConflict = false; ++topicRequest;
    guide.job = null; guide.observed = null; guide.topic = ''; guide.detail = null; guide.topics = []; guide.csrf = '';
    mailDraft = null; mailResult = null; mailError = ''; mailBusy = false;
    tasks = []; users = []; usersError = ''; usersNotice = ''; userBusy = false;
    resetSources();
    $('#app').innerHTML = loginHtml();
  }
  async function getSession(refresh = false) {
    if (refresh) sessionRequest = null;
    if (!sessionRequest) sessionRequest = http('/api/session', { cache: 'no-store' }).then(async (r) => {
      if (!r.ok) throw new Error(tr('sessionUnavailable'));
      session = await r.json();
      if (!session || !['local', 'shared'].includes(session.mode)) throw new Error(tr('sessionUnavailable'));
      return session;
    }).catch((error) => { sessionRequest = null; throw error; });
    return sessionRequest;
  }
  function loginHtml() {
    return '<main class="main login-page"><form id="login-form" class="card settings-form" aria-labelledby="login-title"><h1 id="login-title">ISBE · '+esc(tr('login'))+'</h1><p>'+esc(tr('loginNote'))+'</p><fieldset'+(authBusy ? ' disabled' : '')+'><div class="setting-field"><label for="login-username">'+esc(tr('username'))+'</label><input id="login-username" name="username" autocomplete="username" required maxlength="64"></div><div class="setting-field"><label for="login-password">'+esc(tr('password'))+'</label><input id="login-password" name="password" type="password" autocomplete="current-password" required></div><button class="btn primary" type="submit">'+esc(tr('login'))+'</button></fieldset><p role="status" aria-live="polite" class="error">'+esc(tr(authError))+'</p><div class="settings-actions"><button class="btn" type="button" data-act="lang" data-v="zh">中文</button><button class="btn" type="button" data-act="lang" data-v="en">EN</button></div></form></main>';
  }
  async function signIn(e) {
    e.preventDefault(); if (authBusy) return;
    const username = $('#login-username').value, password = $('#login-password').value;
    authBusy = true; authError = ''; $('#login-form fieldset').disabled = true;
    try {
      const current = await getSession(true);
      const response = await http('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': current.csrf_token }, body: JSON.stringify({ username, password }) });
      if (!response.ok) throw new Error();
      session = await response.json(); sessionRequest = Promise.resolve(session); memo.clear();
      guide.observed = readGenerationMarker();
      await render();
      if (guide.observed) await openGuide(guide.observed.topic);
    } catch { authError = 'loginFailed'; }
    finally { authBusy = false; if (!session?.user) { $('#app').innerHTML = loginHtml(); $('#login-username').value = username; $('#login-password')?.focus(); } }
  }
  async function signOut() {
    if (authBusy || mailBusy || settingsSaving || topicBusy || sourceBusy) return;
    if (!leaveSettings('/login') || !leaveTopics('/login') || !leaveSources('/login')) return;
    authBusy = true;
    try {
      const response = await http('/api/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': session.csrf_token }, body: '{}' });
      if (!response.ok) throw new Error();
      expireSession(); authError = ''; await getSession(true); await render();
    } catch { authError = 'actionFailed'; }
    finally { authBusy = false; if (session?.user) await render(); }
  }

  // ---------- icons ----------
  const svg = (d, size = 18) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICON = {
    overview: svg('<circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="4"></circle><path d="M12 12 L18.5 5.5"></path>'),
    reports: svg('<path d="M6 3h9l4 4v14H6z"></path><path d="M9 11h7M9 15h7M9 7h3"></path>'),
    triage: svg('<path d="M3 4h18l-7 8v6l-4 2v-8z"></path>'),
    review: svg('<path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2z"></path><path d="M4 19V5"></path><path d="M9 8h6"></path>'),
    runs: svg('<path d="M3 12h4l3-8 4 16 3-8h4"></path>'),
    settings: svg('<path d="M4 7h16M4 17h16"></path><circle cx="9" cy="7" r="3" style="fill:var(--nav)"></circle><circle cx="15" cy="17" r="3" style="fill:var(--nav)"></circle>'),
    topicSettings: svg('<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"></path>'),
    sources: svg('<path d="M4 5h16v14H4zM4 10h16M9 10v9"></path>'),
    brush: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="stroke:var(--accent)"><path d="M4 20h4L19 9l-4-4L4 16z"></path><path d="M13.5 6.5l4 4"></path></svg>',
    close: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>',
  };
  const BRAND = `<a class="brand" data-link href="/"><svg width="36" height="36" viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="16" fill="none" stroke-width="1.5" style="stroke:var(--line)"></circle><circle cx="18" cy="18" r="9" fill="none" stroke-width="1.5" style="stroke:var(--line)"></circle><path d="M18 18 L18 2 A16 16 0 0 1 31.86 10 Z" style="fill:var(--accent);fill-opacity:.32"></path><circle cx="24.5" cy="11" r="2.4" style="fill:var(--accent)"></circle></svg><span><b>ISBE</b><small>${esc(tr('radar'))}</small></span></a>`;

  // ---------- topic status ----------
  const status = (t) => {
    if (t.last_run && t.last_run.status === 'failed') return { cls: 'danger', label: `${t.last_run.job} ${tr('failed')}`, fg: 'var(--danger)' };
    if (t.drafts) return { cls: 'warn', label: `${t.drafts} ${tr('drafts')}`, fg: 'var(--warn)' };
    if (!t.active) return { cls: 'plain', label: tr('inactive'), fg: 'var(--muted)' };
    return { cls: '', label: tr('healthy'), fg: 'var(--accent)' };
  };
  const runChip = (s) => (s === 'ok' ? 'solid' : s === 'failed' ? 'danger' : 'plain');
  const runColor = (s) => (s === 'ok' ? 'var(--accent)' : s === 'failed' ? 'var(--danger)' : 'var(--text-2)');
  // One line from a run's stats JSON: `found essaies 14, github 6 · new 20`.
  const statsLine = (raw) => {
    let s;
    try { s = JSON.parse(raw || '{}'); } catch { return ''; }
    if (!s || typeof s !== 'object') return '';
    return Object.entries(s).map(([k, v]) => {
      if (k === 'failed' && v && typeof v === 'object') return `failed ${Object.keys(v).length}`;
      if (v && typeof v === 'object') return `${k} ${Object.entries(v).map(([a, b]) => `${a} ${b}`).join(', ')}`;
      return `${k} ${v}`;
    }).join(' · ');
  };

  // ---------- shell ----------
  function shell(page, ov, body) {
    const nav = (href, key) => `<a class="nav" data-link href="${href}"${page === href ? ' aria-current="page"' : ''}>${ICON[key] || ICON.reports}<span style="flex:1">${esc(tr(key))}</span></a>`;
    const pending = ov ? ov.pending.length : 0;
    const review = `<span class="nav" aria-disabled="true" title="${esc(tr('reviewSoon'))}">${ICON.review}<span style="flex:1">${esc(tr('review'))}</span>${pending ? `<span class="badge">${pending}</span>` : ''}</span>`;
    const side = body.side ?? (ov ? `<div class="side-section"><div class="side-label">${esc(tr('topics'))}</div><div class="side-list">${ov.topics.map((t) => `<a class="side-item" data-link href="${isAdmin() && !ownerQuery() ? '/triage' : '/reports'}?topic=${enc(t.id)}${ownerQuery()}"><span class="dot" style="background:${status(t).fg}"></span><span class="mono" style="flex:1">${esc(t.id)}</span><span class="mono muted">${t.kept}</span></a>`).join('')}</div></div>` : '');
    const svc = ov ? [['essaies', ov.services.essaies], ['metrail', ov.services.metrail], ['LLM', ov.services.llm]]
      .map(([name, v]) => `<div class="svc"><span class="dot" style="background:${v ? 'var(--accent)' : 'var(--faint)'}"></span><span>${name}</span><span class="mono muted">${esc(typeof v === 'string' ? v : v ? tr('online') : tr('off'))}</span></div>`).join('') : '';
    return `<nav class="side" aria-label="Main">${BRAND}<div class="navs">${nav('/', 'overview')}${nav('/reports', 'reports')}${nav('/tasks', 'tasks')}${isAdmin() ? nav('/triage', 'triage') + review + nav('/runs', 'runs') : ''}${nav('/topics', 'topicSettings')}${isAdmin() ? nav('/sources', 'sources') + nav('/settings', 'settings') + (session?.mode === 'shared' ? nav('/users', 'users') : '') : ''}</div>${side}
      <div class="side-foot">${isAdmin() ? svc : ''}${isAdmin() && ov ? `<a data-link href="/settings" class="setting-hint">${esc(tr(ov.services.llm ? 'llmReady' : 'llmIncomplete'))}</a>` : ''}${isAdmin() ? `<a href="/help" class="setting-hint">${esc(tr('help'))}</a>` : ''}
        ${session?.mode === 'shared' ? `<span class="setting-hint">${esc(session.user.username)}</span><button type="button" class="btn" data-act="logout">${esc(tr('logout'))}</button><span role="status" class="error">${esc(tr(authError))}</span>` : ''}
        <button type="button" class="btn" data-act="panel" aria-expanded="${panel}">${ICON.brush}${esc(tr('customize'))}</button>
        <div class="seg" role="group" aria-label="Language"><button type="button" data-act="lang" data-v="zh" aria-pressed="${prefs.lang === 'zh'}">中文</button><button type="button" data-act="lang" data-v="en" aria-pressed="${prefs.lang === 'en'}">EN</button></div>
      </div></nav>
      <main class="main">${body.main}</main>${panel ? customPanel() : ''}<dialog id="report-guide" class="report-guide" aria-labelledby="guide-title"></dialog>`;
  }

  // ---------- overview ----------
  function radar(ts) {
    const max = Math.max(1, ...ts.map((t) => t.kept));
    const pts = ts.map((t, i) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(ts.length, 1);
      const r = 30 + 150 * (t.kept / max);
      return { t, s: status(t), x: 200 + r * Math.cos(a), y: 200 + r * Math.sin(a), size: 4 + 4 * (t.kept / max) };
    });
    const dots = pts.map(({ t, s, x, y, size }) => {
      const right = x >= 200;
      const label = `<text x="${(x + (right ? 12 : -12)).toFixed(1)}" y="${(y + 4).toFixed(1)}" text-anchor="${right ? 'start' : 'end'}" font-size="12" font-family="var(--mono)" style="fill:${s.cls ? s.fg : 'var(--text-2)'}">${esc(t.id)} · ${t.kept}</text>`;
      const dot = s.cls === 'danger'
        ? `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${size.toFixed(1)}" fill="none" stroke-width="2" style="stroke:var(--danger)"></circle>`
        : `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${size.toFixed(1)}" style="fill:${s.fg}"></circle>`;
      return dot + label;
    }).join('');
    const aria = ts.map((t) => `${t.id} ${t.kept}`).join(', ');
    return `<svg class="radar" viewBox="0 0 400 400" role="img" aria-label="${esc(aria)}"><defs><linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0"><stop offset="0" style="stop-color:var(--accent);stop-opacity:0"></stop><stop offset="1" style="stop-color:var(--accent);stop-opacity:.28"></stop></linearGradient></defs>
      <circle cx="200" cy="200" r="180" style="fill:var(--sunken);stroke:var(--line)"></circle>
      ${[130, 80, 30].map((r) => `<circle cx="200" cy="200" r="${r}" fill="none" style="stroke:var(--line-soft)"></circle>`).join('')}
      <path d="M200 20 V380 M20 200 H380" style="stroke:var(--line-soft)"></path>
      <path d="M200 200 L200 20 A180 180 0 0 1 290 44.1 Z" fill="url(#sweep)"></path>
      ${dots}<circle cx="200" cy="200" r="3" style="fill:var(--text)"></circle></svg>`;
  }

  async function overviewPage(ov) {
    if (!isAdmin()) {
      const data = await api('/api/topics');
      return { main: '<header class="head"><h1 class="title">'+esc(tr('overview'))+'</h1><button type="button" class="btn primary" data-act="guideOpen" data-fresh="true">'+esc(tr('guideOpen'))+'</button></header><section class="card"><h2>'+esc(tr('topics'))+'</h2><div class="list">'+(data.topics.map((t) => '<a class="li" data-link href="/reports?topic='+enc(t.id)+'"><span>'+esc(t.label || t.id)+'</span></a>').join('') || '<p>'+esc(tr('topicEmpty'))+'</p>')+'</div><a class="btn" data-link href="/topics">'+esc(tr('topicSettings'))+'</a></section><a class="btn" data-link href="/tasks">'+esc(tr('tasks'))+'</a>' };
    }
    const ts = ov.topics;
    const sum = (k) => ts.reduce((a, t) => a + (t[k] || 0), 0);
    const collected = sum('collected');
    const kept = sum('kept');
    // Items the judge saw; untriaged ones have not passed anything yet.
    const gates = kept + sum('judge_dropped');
    const full = sum('fulltext');
    const since = ts.map((t) => day(t.since)).sort()[0] || '';
    const now = new Date();
    const stamp = now.toLocaleString(locale(), { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    const lmax = Math.log(collected + 1) || 1;
    const funnel = [[tr('fCollected'), collected], [tr('fGates'), gates], [tr('fKept'), kept], [tr('fFull'), full]]
      .map(([l, n], i) => `<div class="funnel"><span class="label">${esc(l)}</span><span class="track"><i style="width:${n ? Math.max(2, (Math.log(n + 1) / lmax) * 100).toFixed(1) : 0}%;opacity:${[0.35, 0.55, 0.8, 1][i]}"></i></span><span class="n">${num(n)}</span></div>`).join('');
    const llm = ov.llm || {};
    const last = ov.runs[0];
    const spark = (daily) => {
      const m = Math.max(1, ...daily);
      return daily.map((v) => `<i style="height:${Math.max(2, Math.round((v / m) * 28))}px${v === 0 ? ';background:var(--line)' : ''}"></i>`).join('');
    };
    const cards = ts.map((t) => {
      const s = status(t);
      return `<a class="tcard" data-link href="/reports?topic=${enc(t.id)}">
        <div class="top"><div style="display:flex;flex-direction:column;gap:4px;min-width:0"><span class="name">${esc(t.label)}</span><span class="meta">${esc(t.id)} · ${esc(t.cadence)} · ${esc(t.flavor)}</span></div><span class="chip ${s.cls}">${esc(s.label)}</span></div>
        <div class="mid"><div style="display:flex;align-items:baseline;gap:8px"><span class="big">${t.kept}</span><span class="muted" style="font-size:13px">${esc(tr('keptOf'))} ${num(t.collected)}</span></div><span class="spark" aria-hidden="true">${spark(t.daily)}</span></div>
        <div class="foot"><span>${esc(tr('last'))} <span class="mono" style="color:var(--text)">${esc(t.report ? t.report.period : '—')}</span></span><span>${t.last_run ? `${esc(t.last_run.job)} · ${esc(when(t.last_run.started_at))}` : esc(tr('noRuns'))}</span></div></a>`;
    }).join('');
    const reports = ov.reports.length ? ov.reports.map((r) => `<a class="li" data-link href="/reports?topic=${enc(r.topic_id)}&period=${enc(r.period)}"><span class="when">${esc(when(r.created_at))}</span><span class="what"><span>${esc((ts.find((t) => t.id === r.topic_id) || {}).label || r.topic_id)} — ${esc(r.period)}</span><small>${esc(r.topic_id)} · ${esc(r.kind)}</small></span><span class="right">${r.media ? r.materials : r.papers} ${esc(tr(r.media ? 'materials' : 'papers'))}</span></a>`).join('') : `<div class="empty">${esc(tr('noReports'))}</div>`;
    const runs = ov.runs.length ? ov.runs.map((r) => `<div class="li"><span class="when">${esc(when(r.started_at))}</span><span class="dot" style="width:9px;height:9px;background:${runColor(r.status)}"></span><span class="what"><span><span class="mono">${esc(r.topic_id || '—')}</span> <span class="muted">${esc(r.job)}</span></span><small${r.status === 'failed' ? ' style="color:var(--danger)"' : ''}>${esc(r.error || statsLine(r.stats))}</small></span><span class="right muted">${esc(dur(r.started_at, r.finished_at))}</span></div>`).join('') : `<div class="empty">${esc(tr('noRuns'))}</div>`;
    return {
      main: `<header class="head"><div style="display:flex;flex-direction:column;gap:8px"><div class="eyebrow">${esc(stamp)}</div><h1 class="title">${esc(tr('ovTitle'))}</h1>
          <div class="sub">${ts.length} ${esc(tr('topicsN'))} · <span style="color:var(--accent)">${num(kept)} ${esc(tr('kept'))}</span> ${esc(tr('of'))} ${num(collected)} ${esc(tr('collected'))} · ${esc(tr('window'))} ${esc(since)} → ${esc(day(ov.now))}</div></div></header>
        <section class="row" aria-label="This window">
          <div class="card grow"><div class="card-head"><h2>${esc(tr('signal'))}</h2><span>${esc(tr('signalNote'))}</span></div>${ts.length ? radar(ts) : `<div class="empty">${esc(tr('noItems'))}</div>`}
            <div class="legend"><span><span class="dot"></span>${esc(tr('healthy'))}</span><span><span class="dot" style="background:var(--warn)"></span>${esc(tr('draftWaiting'))}</span><span><span class="dot" style="background:transparent;border:2px solid var(--danger);box-sizing:border-box;width:9px;height:9px"></span>${esc(tr('lastFailed'))}</span></div></div>
          <div class="grow" style="display:flex;flex-direction:column;gap:20px;min-width:0">
            <div class="card"><div class="card-head"><h2>${esc(tr('pipeline'))}</h2><span>${esc(tr('logScale'))}</span></div>${funnel}</div>
            <div class="stats">
              <div class="stat${ov.pending.length ? ' warn' : ''}"><span class="k">${esc(tr('awaiting'))}</span><span class="v">${ov.pending.length} ${esc(tr('draftsN'))}</span><span class="d">${esc(tr('draftsNote'))}</span></div>
              <a class="stat" data-link href="/runs"><span class="k">${esc(tr('llmWeek'))}</span><span class="v">${compact((llm.tokens_in || 0) + (llm.tokens_out || 0))}</span><span class="d">tokens · ${num(llm.calls)} ${esc(tr('calls'))}</span></a>
              <a class="stat" data-link href="/runs"><span class="k">${esc(tr('lastRun'))}</span><span class="v">${esc(last ? when(last.started_at) : '—')}</span><span class="d">${last ? `${esc(last.topic_id || '—')} · ${esc(last.job)} · ${esc(last.status)}` : esc(tr('none'))}</span></a>
            </div></div></section>
        <section style="display:flex;flex-direction:column;gap:14px" aria-labelledby="topics-h"><div class="card-head"><h2 id="topics-h" style="margin:0;font-family:var(--serif);font-size:24px;font-weight:600">${esc(tr('topics'))}</h2><span>${esc(tr('topicsNote'))}</span></div><div class="topics">${cards}</div></section>
        <section class="row"><div class="card grow"><h2>${esc(tr('latestReports'))}</h2><div class="list">${reports}</div></div>
          <div class="card grow"><div class="card-head"><h2>${esc(tr('recentRuns'))}</h2><a data-link href="/runs" style="font-size:13px">${esc(tr('ledger'))}</a></div><div class="list">${runs}</div></div></section>`,
    };
  }

  // ---------- reports: the shelf, then one issue at a time ----------
  const reportOwner = () => isAdmin() ? new URLSearchParams(location.search).get('owner') || '' : '';
  const ownerQuery = (owner = reportOwner()) => owner && owner !== session?.user?.id ? `&owner=${enc(owner)}` : '';
  const issueHref = (topic, period, owner = reportOwner()) => `/reports?topic=${enc(topic)}&period=${enc(period)}${ownerQuery(owner)}`;

  function firstReportGuide(topic = '') {
    if (ownerQuery()) return '';
    return `<section class="card first-report-guide"><h2>${esc(tr('firstReport'))}</h2><p>${esc(tr('guideIntro'))}</p><button type="button" class="btn primary" data-act="guideOpen" data-topic="${esc(topic)}">${esc(tr('guideOpen'))}</button></section>`;
  }

  // ---------- persistent tasks and administrator accounts ----------
  let tasks = [], tasksTimer = null, tasksError = '', taskBusy = '', tasksRequest = 0;
  const taskStateKey = (state) => 'task' + state[0].toUpperCase() + state.slice(1);
  function taskListHtml() {
    const rows = (items) => items.map((job) => '<article class="card task-card"><div class="card-head"><h3>'+esc(job.topic)+'</h3><span class="chip '+(['failed','interrupted'].includes(job.state) ? 'danger' : '')+'">'+esc(tr(taskStateKey(job.state)))+'</span></div><p class="setting-hint">'+esc(tr('taskCreated'))+' '+esc(when(job.created_at))+(isAdmin() ? ' · '+esc(tr('taskOwner'))+' '+esc(job.owner) : '')+'</p><p>'+esc(job.state === 'queued' ? tr('taskPosition', { n: job.position ?? '—' }) : generationStages.includes(job.stage) ? tr('guide'+job.stage[0].toUpperCase()+job.stage.slice(1)) : tr(taskStateKey(job.state)))+'</p>'+(['failed','interrupted'].includes(job.state) ? '<p class="error">'+esc(tr(job.state === 'interrupted' ? 'guideInterrupted' : 'guideFailed'))+'</p>' : '')+'<div class="settings-actions">'+(job.result?.topic && job.result?.period ? '<a class="btn primary" data-link href="'+issueHref(job.result.topic, job.result.period, job.owner)+'">'+esc(tr('guideOpenReport'))+'</a>' : '')+(job.state === 'queued' ? '<button class="btn" type="button" id="cancel-'+esc(job.id)+'" data-act="taskCancel" data-id="'+esc(job.id)+'"'+(taskBusy ? ' disabled' : '')+'>'+esc(tr('taskCancel'))+'</button>' : '')+(job.owner === session?.user?.id ? '<button class="btn" type="button" data-act="taskOpen" data-id="'+esc(job.id)+'">'+esc(tr('guideCheck'))+'</button>' : '')+'</div></article>').join('');
    return '<p class="error" role="status" aria-live="polite">'+esc(tr(tasksError))+'</p>'+[true, false].map((active) => {
      const items = tasks.filter((job) => activeTask(job) === active);
      const content = items.length ? rows(items) : tasksError ? '' : '<p class="empty">'+esc(tr(active ? 'taskActiveEmpty' : 'taskHistoryEmpty'))+'</p>';
      return '<section class="task-group"><h2>'+esc(tr(active ? 'taskActive' : 'taskHistory'))+'</h2>'+content+'</section>';
    }).join('');
  }
  function updateTasks() {
    if (location.pathname !== '/tasks' || !session?.user) return;
    const list = $('#task-list');
    if (!list) return;
    const active = list.contains?.(document.activeElement) ? document.activeElement.id : '';
    const html = taskListHtml();
    if (list.innerHTML !== html) { list.innerHTML = html; if (active) ($('#'+active) || $('#tasks-heading'))?.focus(); }
  }
  function scheduleTasks() {
    clearTimeout(tasksTimer);
    if (location.pathname === '/tasks' && session?.user && tasks.some(activeTask) && !tasksError) tasksTimer = setTimeout(refreshTasks, 2000);
  }
  async function refreshTasks() {
    const request = ++tasksRequest;
    try {
      const response = await http('/api/tasks', { cache: 'no-store' });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (request !== tasksRequest || !session?.user) return;
      tasks = data.tasks; session.csrf_token = data.csrf_token || session.csrf_token; tasksError = '';
    } catch { if (request === tasksRequest) tasksError = 'guideUnavailable'; }
    updateTasks(); scheduleTasks();
  }
  async function tasksPage() {
    await refreshTasks();
    return { main: '<header class="head"><div class="settings-intro"><h1 id="tasks-heading" tabindex="-1" class="title">'+esc(tr('tasks'))+'</h1><p class="sub">'+esc(tr('tasksNote'))+'</p></div><div class="settings-actions"><button class="btn" type="button" data-act="tasksRefresh">'+esc(tr('taskRefresh'))+'</button><button class="btn primary" type="button" data-act="guideOpen" data-fresh="true">'+esc(tr('guideOpen'))+'</button></div></header><div id="task-list" class="task-list">'+taskListHtml()+'</div>' };
  }
  async function cancelTask(button) {
    if (taskBusy || !window.confirm(tr('taskCancelConfirm'))) return;
    taskBusy = button.dataset.id; updateTasks();
    try {
      const response = await http('/api/tasks/'+enc(taskBusy)+'/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': session.csrf_token }, body: '{}' });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (guide.job?.id === data.job.id) { generationState(data); updateGuide(); }
      await refreshTasks();
    } catch { tasksError = 'actionFailed'; }
    finally { taskBusy = ''; updateTasks(); }
  }
  let users = [], usersError = '', usersNotice = '', userBusy = false;
  async function usersPage() {
    const response = await http('/api/users', { cache: 'no-store' });
    if (!response.ok) throw new Error(tr('actionFailed'));
    const data = await response.json(); users = data.users; session.csrf_token = data.csrf_token || session.csrf_token;
    return { main: '<header class="head"><div class="settings-intro"><h1 class="title">'+esc(tr('users'))+'</h1><p class="sub">'+esc(tr('usersNote'))+'</p></div></header><p id="users-status" role="status" tabindex="-1" class="'+(usersError ? 'error' : 'topic-status')+'">'+esc(tr(usersError || usersNotice))+'</p><form id="user-create" class="card settings-form"><h2>'+esc(tr('userCreate'))+'</h2><fieldset'+(userBusy ? ' disabled' : '')+'><div class="settings-grid"><div class="setting-field"><label for="new-username">'+esc(tr('username'))+'</label><input id="new-username" name="username" autocomplete="off" required minlength="3" maxlength="64"></div><div class="setting-field"><label for="new-password">'+esc(tr('userPassword'))+'</label><input id="new-password" name="password" type="password" autocomplete="new-password" required minlength="12" maxlength="1024"></div></div><button class="btn primary" type="submit">'+esc(tr('userCreate'))+'</button></fieldset></form><section class="task-list">'+users.map((user) => '<article class="card"><div class="card-head"><h2>'+esc(user.username)+'</h2><span class="chip">'+esc(tr(user.role === 'admin' ? 'userAdmin' : 'userRegular'))+' · '+esc(tr(user.active ? 'userActive' : 'userInactive'))+'</span></div><button class="btn" type="button" data-act="userActive" data-id="'+esc(user.id)+'" data-active="'+(!user.active)+'"'+(userBusy ? ' disabled' : '')+'>'+esc(tr(user.active ? 'userDeactivate' : 'userActivate'))+'</button><form class="settings-form" data-user-password="'+esc(user.id)+'"><fieldset'+(userBusy ? ' disabled' : '')+'><div class="setting-field"><label for="password-'+esc(user.id)+'">'+esc(tr('userPassword'))+'</label><input id="password-'+esc(user.id)+'" type="password" autocomplete="new-password" required minlength="12" maxlength="1024"></div><button class="btn" type="submit">'+esc(tr('userReset'))+'</button></fieldset></form></article>').join('')+'</section>' };
  }
  async function changeUser(url, method, body) {
    if (userBusy) return;
    userBusy = true; usersError = ''; usersNotice = ''; await render();
    try {
      const response = await http(url, { method, headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': session.csrf_token }, body: JSON.stringify(body) });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (method === 'PUT' && data.user.id === session?.user?.id) { expireSession(); await getSession(true); return; }
      usersNotice = method === 'POST' ? 'userCreated' : 'userUpdated';
    } catch { usersError = 'actionFailed'; }
    finally { userBusy = false; await render(); $('#users-status')?.focus(); }
  }
  function createUser(e) {
    e.preventDefault();
    return changeUser('/api/users', 'POST', { username: $('#new-username').value, password: $('#new-password').value });
  }
  function resetUserPassword(e) {
    e.preventDefault();
    const id = e.target.dataset.userPassword;
    if (!window.confirm(tr('userPasswordConfirm'))) return;
    return changeUser('/api/users/'+enc(id), 'PUT', { password: $('#password-'+id).value });
  }

  // The per-user marker recovers the task or the idempotent submission after refresh.
  const readGenerationMarker = () => { try { return JSON.parse(sessionStorage.getItem(session?.user?.id === 'local' ? 'isbe.generation' : 'isbe.generation.' + session?.user?.id) || 'null'); } catch { return null; } };
  const guide = { open: false, step: 1, topic: '', topics: [], detail: null, model: false, loading: false, starting: false, checked: false, csrf: '', job: null, lastJobId: null, error: '', statusError: '', interrupted: false, observed: null, timer: null, request: 0 };
  const generationStages = ['collect', 'triage', 'enrich', 'digest'];
  const guideDraft = () => settingsDirty() || topicDirty() || settingsSaving || topicBusy;
  const guideSourceReady = () => guide.detail?.id === guide.topic && !guide.detail?.error && !!guide.detail?.config?.arxiv?.categories?.length && !!guide.detail?.config?.arxiv?.include_keywords?.length;
  const guideCanStart = () => guide.checked && guide.csrf && !guide.loading && !guide.starting && !guide.statusError && !guide.error && !activeTask(guide.job) && guide.model && guideSourceReady() && !guideDraft();
  function rememberGeneration(marker) {
    guide.observed = marker;
    try { if (marker) sessionStorage.setItem(session.user.id === 'local' ? 'isbe.generation' : 'isbe.generation.' + session.user.id, JSON.stringify(marker)); else sessionStorage.removeItem(session.user.id === 'local' ? 'isbe.generation' : 'isbe.generation.' + session.user.id); } catch { /* private mode */ }
  }

  function generationState(data) {
    const job = data.job;
    if (typeof data.csrf_token === 'string') guide.csrf = data.csrf_token;
    if (job && (!job.id || !job.topic || !['queued','running','succeeded','failed','interrupted','cancelled'].includes(job.state) || !['queued', ...generationStages].includes(job.stage))) throw new Error();
    guide.job = job || null; guide.lastJobId = job?.id || guide.lastJobId;
    guide.interrupted = job?.state === 'interrupted';
    if (job) guide.topic = job.topic;
    if (activeTask(job)) rememberGeneration({ id: job.id, topic: job.topic });
    else if (job) rememberGeneration(null);
    guide.checked = true; guide.statusError = '';
    if (job || guide.observed) guide.step = 3;
  }

  function guideHtml() {
    const job = guide.job;
    const busy = guide.starting || activeTask(job);
    const settingsLink = isAdmin() ? `<a class="btn" data-link href="/settings">${esc(tr('settings'))} →</a>` : '';
    const topicsLink = `<a class="btn" data-link href="/topics">${esc(tr('topicSettings'))} →</a>`;
    const heading = `<header class="guide-head"><div><p class="eyebrow">${esc(tr('guideStep', { n: guide.step }))}</p><h2 id="guide-title" tabindex="-1">${esc(tr('firstReport'))}</h2></div><button type="button" id="guide-close" class="btn" data-act="guideClose" aria-label="${esc(tr('guideClose'))}">${ICON.close}</button></header><ol class="guide-steps">${['guideModel', 'guideTopic', 'guideGenerate'].map((key, i) => `<li${guide.step === i + 1 ? ' aria-current="step"' : ''}><span>${i + 1}</span>${esc(tr(key))}</li>`).join('')}</ol>`;
    const warnings = `${guideDraft() ? `<p class="topic-warning">${esc(tr('guideSavedOnly'))}</p>` : ''}${guide.error ? `<p role="alert" class="error">${esc(tr(guide.error))}</p>` : ''}`;
    let body;
    if (guide.loading) body = `<p role="status">${esc(tr('loading'))}</p>`;
    else if (guide.step === 1) body = `<h3>${esc(tr('guideModel'))}</h3><p>${esc(tr(isAdmin() ? 'firstModel' : 'modelManaged'))}</p><p class="${guide.model ? 'guide-ready' : 'topic-warning'}">${esc(tr(guide.model ? 'llmReady' : isAdmin() ? 'llmIncomplete' : 'modelContact'))}</p>${settingsLink}`;
    else if (guide.step === 2) body = `<h3>${esc(tr('guideTopic'))}</h3><div class="setting-field"><label for="guide-topic">${esc(tr('topic'))}</label><select id="guide-topic" data-change="guideTopic"${busy ? ' disabled' : ''}><option value="">${esc(tr('firstChoose'))}</option>${guide.topics.map((t) => `<option value="${esc(t.id)}"${t.id === guide.topic ? ' selected' : ''}>${esc(t.label || t.id)}</option>`).join('')}</select></div><p>${esc(tr(!guide.topic ? 'firstChoose' : guide.detail?.config && !guide.detail.config.arxiv ? 'firstArticles' : guideSourceReady() ? 'guideSourceReady' : 'guideSourceInvalid'))}</p>${topicsLink}`;
    else {
      const noData = job?.error === "No papers remain in this topic's current window. Review its sources, keywords and filters in Topics, then try again.";
      const outcome = guide.statusError || (guide.interrupted ? 'guideInterrupted' : guide.starting ? 'guideStarting' : job?.state === 'queued' ? 'taskQueued' : job?.state === 'cancelled' ? 'taskCancelled' : job?.state === 'running' ? 'guideRunning' : job?.state === 'succeeded' && job.result?.topic && job.result?.period ? 'guideComplete' : job ? noData ? 'guideNoData' : 'guideFailed' : guide.observed?.requestId ? 'taskUnknown' : 'guideStartNote');
      const index = generationStages.indexOf(job?.stage);
      body = `<h3>${esc(tr('guideGenerate'))}</h3><p><b>${esc(guide.topics.find((t) => t.id === (job?.topic || guide.topic))?.label || job?.topic || guide.topic)}</b></p><p id="generation-status" role="status" aria-live="polite" aria-atomic="true">${esc(tr(outcome, { topic: job?.topic || guide.topic }))}${job?.state === 'queued' ? ` · ${esc(tr('taskPosition', { n: job.position ?? '—' }))}` : ''}</p>${job && !guide.interrupted ? `<ol class="generation-stages">${generationStages.map((stage, i) => {
        const state = job.state === 'succeeded' || i < index ? 'guideDone' : i === index ? !activeTask(job) ? 'guideStopped' : 'guideCurrent' : 'guideWaiting';
        return `<li${i === index && job.state === 'running' ? ' aria-current="step"' : ''}><span>${esc(tr(`guide${stage[0].toUpperCase()}${stage.slice(1)}`))}</span><small>${esc(tr(state))}</small></li>`;
      }).join('')}</ol>` : ''}${busy ? `<p class="setting-hint">${esc(tr('guideBackground'))}</p><a class="btn" data-link href="/tasks">${esc(tr('tasks'))}</a>` : ''}${job?.state === 'succeeded' && job.result?.topic && job.result?.period ? `<a class="btn primary" data-link href="${issueHref(job.result.topic, job.result.period, job.owner)}">${esc(tr('guideOpenReport'))} →</a>` : guide.interrupted ? `<a class="btn" data-link href="/reports?topic=${enc(guide.topic)}">${esc(tr('guideViewReports'))} →</a>` : ''}${job?.state === 'failed' ? `<div class="settings-actions">${noData && isAdmin() && job.owner === session?.user?.id ? `<a class="btn" data-link href="/triage?topic=${enc(job.topic)}">${esc(tr('guideReviewTriage'))} →</a>` : ''}${topicsLink}${settingsLink}</div>` : ''}`;
    }
    return `${heading}<section class="guide-body">${body}${warnings}</section><footer class="guide-actions">${guide.step > 1 && !busy ? `<button type="button" id="guide-back" class="btn" data-act="guideBack">${esc(tr('guideBack'))}</button>` : ''}<button type="button" id="guide-check" class="btn" data-act="guideCheck"${guide.loading || guide.starting ? ' disabled' : ''}>${esc(tr('guideCheck'))}</button>${guide.step < 3 ? `<button type="button" id="guide-next" class="btn primary" data-act="guideNext"${guide.loading || guide.error || guideDraft() || (guide.step === 1 ? !guide.model : !guideSourceReady()) ? ' disabled' : ''}>${esc(tr('guideNext'))} →</button>` : job?.state === 'succeeded' || activeTask(job) ? `<button type="button" class="btn" data-act="guideAgain">${esc(tr(activeTask(job) ? 'taskNew' : 'guideAgain'))}</button>` : `<button type="button" id="guide-start" class="btn primary" data-act="guideStart"${guideCanStart() ? '' : ' disabled'}>${esc(tr(guide.starting ? 'guideStarting' : 'guideGenerate'))}</button>`}</footer>`;
  }

  function updateGuide(focus = false) {
    if (!guide.open) return;
    const dialog = $('#report-guide');
    if (!dialog) return;
    const active = dialog.contains(document.activeElement) ? document.activeElement.id : '';
    const html = guideHtml();
    if (dialog.innerHTML !== html) {
      dialog.innerHTML = html;
      if (active) { const target = $(`#${active}`); (target && !target.disabled ? target : $('#guide-title'))?.focus(); }
    }
    if (!dialog.open) dialog.showModal();
    if (focus) $('#guide-title')?.focus();
  }

  function closeGuide() {
    guide.open = false;
    clearTimeout(guide.timer);
    $('#report-guide')?.close();
  }

  function scheduleGenerationCheck() {
    clearTimeout(guide.timer);
    if (guide.open && activeTask(guide.job) && !guide.statusError) guide.timer = setTimeout(checkGeneration, 2000);
  }

  async function checkGeneration() {
    const request = guide.request;
    try {
      const id = guide.job?.id || guide.observed?.id;
      const response = await http(id ? '/api/tasks/'+enc(id) : '/api/tasks', { cache: 'no-store' });
      if (!response.ok && response.status !== 404) throw new Error();
      const data = response.status === 404 ? { job: null } : await response.json();
      if (request !== guide.request || !session?.user) return;
      if (!id) data.job = guide.observed?.requestId ? null : (data.tasks || []).find((job) => job.owner === session.user.id && (!guide.topic || job.topic === guide.topic));
      generationState(data);
      if (id && !data.job) { guide.interrupted = true; guide.step = 3; rememberGeneration(null); }
    } catch { if (request === guide.request && session?.user) { guide.checked = false; guide.statusError = 'guideUnavailable'; guide.step = 3; } }
    updateGuide(); scheduleGenerationCheck();
  }

  async function loadGuide() {
    const request = ++guide.request;
    guide.loading = true; guide.error = ''; updateGuide();
    try {
      const responses = await Promise.all(['/api/session', '/api/topics'].map((url) => http(url, { cache: 'no-store' })));
      if (responses.some((r) => !r.ok)) throw new Error();
      const [currentSession, topics] = await Promise.all(responses.map((r) => r.json()));
      if (request !== guide.request) return;
      guide.model = currentSession.llm?.ready === true; guide.csrf = currentSession.csrf_token || guide.csrf;
      guide.topics = topics.topics;
      if (!guide.topic) guide.topic = guide.job?.topic || guide.topics[0]?.id || '';
      guide.detail = null;
      if (guide.topic) {
        const response = await http(`/api/topics/${enc(guide.topic)}`, { cache: 'no-store' });
        if (!response.ok) throw new Error();
        const detail = await response.json();
        if (request !== guide.request) return;
        guide.detail = detail;
      }
    } catch { if (request === guide.request) guide.error = 'guideLoadFailed'; }
    finally { if (request === guide.request) { guide.loading = false; updateGuide(); } }
  }

  async function openGuide(topic = '', fresh = false) {
    if (!session?.user || ownerQuery()) return;
    ++guide.request;
    if (fresh || (topic && topic !== guide.topic && topic !== guide.observed?.topic)) { guide.job = null; guide.observed = null; guide.lastJobId = null; }
    guide.open = true; guide.topic = topic || (fresh ? '' : guide.topic); guide.step = 1;
    guide.error = ''; guide.statusError = ''; guide.checked = false; guide.interrupted = false;
    updateGuide(true);
    if (!fresh) await checkGeneration();
    else { guide.checked = true; guide.csrf = session.csrf_token; }
    await loadGuide();
  }
  async function openTask(button) {
    const job = tasks.find((item) => item.id === button.dataset.id);
    if (!job || job.owner !== session?.user?.id) return;
    guide.job = job; guide.observed = { id: job.id, topic: job.topic }; guide.topic = job.topic;
    await openGuide(job.topic);
  }
  async function startGeneration() {
    if (!guideCanStart()) return;
    const requestId = guide.observed?.requestId || Array.from(crypto.getRandomValues(new Uint8Array(16)), (v) => v.toString(16).padStart(2, '0')).join('');
    const owner = session.user.id;
    guide.starting = true; guide.error = ''; guide.interrupted = false;
    rememberGeneration({ topic: guide.topic, requestId });
    updateGuide();
    try {
      const response = await http('/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': guide.csrf }, body: JSON.stringify({ topic: guide.topic, request_id: requestId }) });
      const data = await response.json();
      if (session?.user?.id !== owner) return;
      if (response.ok || (response.status === 409 && data.code === 'busy')) {
        if (!data.job) throw new Error();
        generationState(data);
      } else if (response.status < 500) {
        rememberGeneration(null);
        guide.error = { configuration: 'llmIncomplete', invalid_topic: 'guideSourceInvalid', unsupported_topic: 'firstArticles' }[data.code] || 'guideStartFailed';
      } else await checkGeneration();
    } catch { if (session?.user?.id === owner) await checkGeneration(); }
    finally { guide.starting = false; if (session?.user?.id === owner) { if (guide.detail?.id !== guide.topic) await loadGuide(); updateGuide(); scheduleGenerationCheck(); if (location.pathname === '/tasks') await refreshTasks(); } }
  }

  async function shelfPage(ov, q) {
    const all = await api(`/api/reports${ownerQuery() ? '?' + ownerQuery().slice(1) : ''}`);
    const topic = q.get('topic') || '';
    if (!all.some((r) => !topic || r.topic_id === topic)) return { main: `<div class="card"><div class="empty">${esc(tr('noReports'))}</div></div>${firstReportGuide(topic)}` };
    const topics = [...new Set(all.map((r) => r.topic_id))];
    const chips = [['', tr('all'), all.length], ...topics.map((t) => [t, t, all.filter((r) => r.topic_id === t).length])]
      .map(([k, l, n]) => `<a data-link href="/reports${k ? `?topic=${enc(k)}${ownerQuery()}` : ownerQuery() ? `?${ownerQuery().slice(1)}` : ''}"${k === topic ? ' aria-current="page"' : ''}>${esc(l)} <span>${n}</span></a>`).join('');
    const cover = (r) => `<a class="cover" data-link href="${issueHref(r.topic_id, r.period)}">
        <span class="cv-top"><span>ISBE · ${esc(tr(r.kind))}</span>${r.imported ? '<span class="cv-badge">v1</span>' : ''}</span>
        ${r.show_period === false ? '' : `<span class="cv-period">${esc(r.period)}</span>`}<span class="cv-label">${esc(r.label || r.topic_id)}</span>
        ${r.window ? `<span class="cv-window">${esc(r.window)}</span>` : ''}
        <ul class="cv-tldr">${r.tldr.map((b) => `<li>${esc(b)}</li>`).join('')}</ul>
        <span class="cv-foot"><span>${r.kind === 'research' || r.media ? r.materials : r.papers} ${esc(tr(r.kind === 'research' || r.media ? 'materials' : 'papers'))}</span><span>${esc(day(r.created_at))}</span></span></a>`;
    const groups = (topic ? [topic] : topics).map((t) => {
      const issues = all.filter((r) => r.topic_id === t);
      return `<section class="shelf-group" aria-labelledby="g-${esc(t)}"><div class="card-head"><h2 id="g-${esc(t)}" class="shelf-title">${esc(issues[0].label || t)} <span class="mono muted" style="font-size:13px;font-weight:400">${esc(t)}</span></h2><span>${issues.length} ${esc(tr('issuesN'))}</span></div>
        <div class="shelf">${issues.map(cover).join('')}</div></section>`;
    }).join('');
    return {
      main: `<header class="head"><div style="display:flex;flex-direction:column;gap:6px"><div class="eyebrow">${esc(tr('shelfEyebrow'))}</div><h1 class="title">${esc(tr('shelf'))}</h1></div>${!ownerQuery() ? `<button type="button" class="btn" data-act="guideOpen" data-topic="${esc(topic)}">${esc(tr('guideOpen'))}</button>` : ''}</header>
        <nav class="filters" aria-label="${esc(tr('topics'))}">${chips}</nav>${groups}`,
    };
  }

  function attachReportArticles(doc, report) {
    if (!isAdmin() || ownerQuery() || !report.articles?.length) return;
    const articles = new Map(report.articles.map((item) => [item.url, item.id]));
    for (const heading of doc.querySelectorAll('h3')) {
      const link = heading.querySelector('a[href]');
      const id = link && articles.get(link.getAttribute('href'));
      if (!id) continue;
      const sources = new Map([[id, link.textContent || '']]);
      let after = heading;
      while (after.nextElementSibling && !['H2', 'H3', 'HR'].includes(after.nextElementSibling.tagName)) {
        after = after.nextElementSibling;
        for (const source of after.querySelectorAll('a[href]')) {
          const sourceId = articles.get(source.getAttribute('href'));
          if (sourceId) sources.set(sourceId, source.textContent || '');
        }
      }
      after.insertAdjacentHTML('afterend', [...sources].map(([sourceId, title]) => {
        const label = sources.size > 1 ? title : '';
        return `<details class="media-original" data-topic="${esc(report.topic)}" data-period="${esc(report.period)}" data-material="${esc(sourceId)}" data-label="${esc(label)}"><summary>${esc(tr('articleExpand'))}${label ? ' · ' + esc(label) : ''}</summary><div class="article-body" aria-live="polite"></div></details>`;
      }).join(''));
    }
  }

  async function toggleReportArticle(details) {
    details.querySelector('summary').textContent = tr(details.open ? 'articleCollapse' : 'articleExpand') + (details.dataset.label ? ' · ' + details.dataset.label : '');
    if (!details.open || details.dataset.loading || details.dataset.loaded) return;
    const body = details.querySelector('.article-body');
    details.dataset.loading = '1';
    body.textContent = tr('articleLoading');
    body.setAttribute('aria-busy', 'true');
    try {
      const response = await http('/api/sources/article', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': session.csrf_token },
        body: JSON.stringify({ topic: details.dataset.topic, period: details.dataset.period, id: details.dataset.material }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        body.textContent = tr(error.code === 'unsupported_body' ? 'articleUnsupported' : error.code === 'article_unavailable' ? 'articleStoredUnavailable' : 'articleUnavailable');
        return;
      }
      const article = await response.json();
      if (!Array.isArray(article.paragraphs) || !article.paragraphs.length || article.paragraphs.some((p) => typeof p !== 'string')) throw new Error('unavailable');
      body.innerHTML = `<p class="article-read-at">${esc(tr(article.cached || article.frozen ? 'articleSavedAt' : 'articleReadAt', { time: sourceDate(article.fetched_at) }))}</p>${article.paragraphs.map((p) => `<p>${esc(p)}</p>`).join('')}`;
      details.dataset.loaded = '1';
    } catch {
      body.textContent = tr('articleUnavailable');
    } finally {
      delete details.dataset.loading;
      body.setAttribute('aria-busy', 'false');
    }
  }

  async function readerPage(ov, q) {
    const r = await api(`/api/report?topic=${enc(q.get('topic') || '')}&period=${enc(q.get('period') || '')}${ownerQuery()}`);
    if (!r) return { main: `<div class="card"><div class="empty">${esc(tr('noReports'))}</div></div>${firstReportGuide(q.get('topic') || '')}` };
    // Number the sections for the side table of contents; the HTML was sanitized server-side.
    const doc = new DOMParser().parseFromString(r.html, 'text/html');
    attachReportArticles(doc, r);
    const toc = [...doc.querySelectorAll('h2')].map((h, i) => { h.id = `s${i}`; return { id: h.id, label: h.textContent }; });
    const label = r.label || r.topic;
    // Issues come newest first: the next one in the list is the older issue.
    const at = r.issues.findIndex((i) => i.period === r.period);
    const older = r.issues[at + 1];
    const newer = at > 0 ? r.issues[at - 1] : null;
    const turn = (i, cls, text) => (i ? `<a class="btn ${cls}" data-link href="${issueHref(r.topic, i.period)}">${text}</a>` : '');
    const pager = (cls) => `${turn(older, cls, `← ${esc(older ? older.period : '')}`)}${turn(newer, cls, `${esc(newer ? newer.period : '')} →`)}`;
    const issues = r.issues.map((i) => `<a class="issue" data-link href="${issueHref(r.topic, i.period)}"${i.period === r.period ? ' aria-current="page"' : ''}><span>${esc(i.period)}</span><span class="muted" style="font-size:12px">${i.media ? `${i.materials} ${esc(tr('materials'))}` : i.kind === 'research' ? esc(tr('research')) : i.papers}</span></a>`).join('');
    return {
      side: `<div class="side-section"><div class="side-label">${esc(tr('onPage'))}</div><div class="side-list">${toc.map((s) => `<a class="side-item" href="#${s.id}">${esc(s.label)}</a>`).join('')}</div></div>`,
      main: `<div class="head no-print" style="align-items:center"><div class="crumbs"><a data-link href="/reports${ownerQuery() ? `?${ownerQuery().slice(1)}` : ''}" style="color:var(--muted)">${esc(tr('shelf'))}</a> / <a data-link href="/reports?topic=${enc(r.topic)}${ownerQuery()}" style="color:var(--muted)">${esc(label)}</a>${r.show_period === false ? '' : ` / <b>${esc(r.period)}</b>`}</div>
          <div style="display:flex;flex-wrap:wrap;gap:10px">${pager('')}
          <button type="button" class="btn primary" data-act="print" data-name="${esc(`${r.topic}-${r.period}`)}">${esc(tr('pdf'))}</button>
          ${!ownerQuery() ? `<button type="button" class="btn" data-act="mailOpen" data-topic="${esc(r.topic)}" data-period="${esc(r.period)}"${mailBusy ? ' disabled' : ''}>${esc(tr('mailOpen'))}</button>` : ''}
          <a class="btn" href="/api/report.md?topic=${enc(r.topic)}&period=${enc(r.period)}${ownerQuery()}" download>${esc(tr('download'))}</a></div></div>
        <div class="row" style="gap:24px"><div style="flex:999 1 560px;min-width:0;max-width:860px;display:flex;flex-direction:column;gap:16px">
          ${mailDraft && !mailDraft.test && mailDraft.topic === r.topic && mailDraft.period === r.period ? mailForm() : ''}
          <article class="paper"><div class="sheet-head"><span>ISBE · ${esc(tr(r.kind))}${r.show_period === false ? '' : ` · ${esc(r.period)}`}</span><span>${esc(label)}</span></div>${doc.body.innerHTML}</article>
          <nav class="pager no-print" aria-label="${esc(tr('issues'))}">${pager('')}</nav></div>
          <aside class="issues no-print" aria-label="Report history"><div class="card" style="gap:4px"><h2 style="margin-bottom:8px">${esc(tr('issues'))}</h2>${issues}</div>
            <div class="card" style="gap:8px;font-size:13px;color:var(--text-2)"><span>${esc(tr('generated'))} ${esc(when(r.created_at))}</span></div></aside></div>`,
    };
  }

  // Without an issue named, Reports is the shelf.
  const reportPage = (ov, q) => (q.get('period') ? readerPage(ov, q) : shelfPage(ov, q));

  // ---------- triage ----------
  const verdictOf = (it) => {
    if (it.verdict === 'kept' && it.contract_sha === '') return { key: 'kept', cls: 'plain', label: tr('keptUnjudged') };
    if (it.verdict === 'kept') return it.tier === 'secondary' ? { key: 'kept', cls: '', label: tr('keptSec') } : { key: 'kept', cls: 'solid', label: tr('keptCore') };
    if (it.verdict === 'judge_dropped') return { key: 'judge', cls: 'warn', label: tr('judgeDropped') };
    if (it.verdict === 'gate_dropped') return { key: 'gate', cls: 'danger', label: tr('gateDropped') };
    return { key: 'none', cls: 'plain', label: tr('untriaged') };
  };
  const trace = (it) => {
    const ok = (step, d) => ({ m: '✓', c: 'var(--accent)', step, d });
    const no = (step, d) => ({ m: '✕', c: 'var(--danger)', step, d });
    const skip = (step, d) => ({ m: '·', c: 'var(--faint)', step, d: d || tr('notReached') });
    const r = it.reason || '';
    if (r.startsWith('no retrieval block')) return [skip('rules', r), ok('judge', 'IN')];
    switch (it.verdict) {
      case 'gate_dropped':
        return r.startsWith('out_of_scope')
          ? [no('out_of_scope', r.split(': ').slice(1).join(': ') || r), skip('require_any'), skip('judge')]
          : [ok('out_of_scope', tr('noHit')), no('require_any', tr('noTerm')), skip('judge')];
      case 'judge_dropped': return [ok('out_of_scope', tr('noHit')), ok('require_any', tr('hit')), no('judge', 'OUT')];
      case 'kept':
        return it.contract_sha === ''
          ? [ok('out_of_scope', tr('noHit')), ok('require_any', tr('hit')), skip('judge', tr('notJudged'))]
          : [ok('out_of_scope', tr('noHit')), ok('require_any', tr('hit')), ok('judge', `IN · ${it.tier || 'core'}`)];
      default: return [skip('out_of_scope'), skip('require_any'), skip('judge')];
    }
  };

  async function triagePage(ov, q) {
    const d = await api(`/api/triage?topic=${enc(q.get('topic') || '')}`);
    if (!d) return { main: `<div class="card"><div class="empty">${esc(tr(q.get('topic') ? 'topicMissing' : 'noItems'))}</div><a data-link href="/topics">${esc(tr('topicSettings'))}</a></div>` };
    const items = d.items;
    const n = (k) => items.filter((it) => verdictOf(it).key === k).length;
    const kept = items.filter((it) => it.verdict === 'kept');
    const sec = kept.filter((it) => it.tier === 'secondary').length;
    const gate = n('gate');
    const triaged = items.filter((it) => it.verdict).length;
    const full = kept.filter((it) => it.fulltext).length;
    const filter = q.get('f') || 'all';
    const shown = items.filter((it) => filter === 'all' || verdictOf(it).key === filter);
    const sel = items.find((it) => it.id === q.get('id')) || shown[0];
    const stages = [
      [tr('stCollected'), items.length, 'var(--text)', tr('stCollectedNote')],
      [tr('stGates'), triaged - gate, 'var(--text)', tr('stGatesNote', { n: gate })],
      [tr('stKept'), kept.length, 'var(--accent)', tr('stKeptNote', { core: kept.length - sec, sec, jd: n('judge') })],
      [tr('stFull'), `${full}/${kept.length}`, 'var(--accent)', tr('stFullNote', { n: full })],
    ].map(([k, v, c, note], i) => `<div><span class="k">${i + 1} · ${esc(k)}</span><span class="v" style="color:${c}">${esc(v)}</span><span class="d">${esc(note)}</span></div>`).join('');
    const chips = [['all', tr('all'), items.length], ['kept', tr('fKeptChip'), n('kept')], ['judge', tr('judgeDropped'), n('judge')], ['gate', tr('gateDropped'), n('gate')]]
      .map(([k, l, c]) => `<button type="button" data-act="param" data-k="f" data-v="${k}" aria-pressed="${filter === k}">${esc(l)} <span>${c}</span></button>`).join('');
    const rows = shown.map((it) => {
      const v = verdictOf(it);
      return `<button type="button" class="trow" data-act="param" data-k="id" data-v="${esc(it.id)}" aria-pressed="${sel && sel.id === it.id}"><span class="chip ${v.cls}">${esc(v.label)}</span><span class="ttl"><span>${esc(it.title)}</span><small>${esc(it.id)} · ${esc((it.categories || []).slice(0, 3).join(' ') || it.source)}</small></span><span class="why">${esc(it.reason || '—')}</span></button>`;
    }).join('');
    let detail = '';
    if (sel) {
      const v = verdictOf(sel);
      const url = safeUrl(sel.url);
      detail = `<aside class="card detail" aria-label="Selected item"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><span class="chip ${v.cls}">${esc(v.label)}</span><span class="mono muted" style="font-size:12px">${esc(sel.id)}</span></div>
        <h2>${esc(sel.title)}</h2><div class="muted" style="font-size:13px">${esc(tr('via'))} ${esc(sel.source)} · ${esc((sel.categories || []).join(' '))} · ${esc(day(sel.published_at))}${sel.fulltext ? ` · ${esc(tr('fulltextYes'))}` : ''}</div>
        <div class="trace">${trace(sel).map((g) => `<div><b style="color:${g.c}">${g.m}</b><span>${esc(g.step)}<small>${esc(g.d)}</small></span></div>`).join('')}</div>
        <div style="display:flex;flex-direction:column;gap:6px"><span class="eyebrow">${esc(tr('judgeSays'))}</span><p class="reason">${esc(sel.reason || '—')}</p></div>
        ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" style="font-size:14px">${esc(tr('open'))}</a>` : ''}</aside>`;
    }
    return {
      main: `<header class="head"><div style="display:flex;flex-direction:column;gap:6px"><div class="eyebrow">${esc(tr('triageEyebrow'))}</div><h1 class="title" style="font-size:clamp(26px,3vw,38px)">${esc(d.topic)} <span class="muted" style="font-weight:400">· ${esc(day(d.since))} → ${esc(day(ov.now))}</span></h1></div>
          <label class="pick">${esc(tr('topic'))}<select data-change="triageTopic">${d.topics.map((t) => `<option${t === d.topic ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label></header>
        <section class="stages" aria-label="Funnel">${stages}</section>
        <div class="filters" role="group" aria-label="Filter items">${chips}</div>
        <div class="row"><div class="table"><div class="scroll"><div class="inner"><div class="trow headrow"><span>${esc(tr('verdict'))}</span><span>${esc(tr('item'))}</span><span>${esc(tr('reason'))}</span></div>${rows || `<div class="empty" style="padding:20px">${esc(tr('noItems'))}</div>`}</div></div></div>${detail}</div>`,
    };
  }

  // ---------- runs ----------
  function runDetails(raw) {
    let stats;
    try { stats = JSON.parse(raw || '{}'); } catch { return ''; }
    const items = [['failed', 'runPaperFailure'], ['figure_failures', 'runFigureFailure'], ['abstract_fallback', 'runAbstractFallback']].flatMap(([key, label]) => {
      const reasons = stats?.[key];
      if (!reasons || typeof reasons !== 'object' || Array.isArray(reasons)) return [];
      return Object.entries(reasons).map(([paper, reason]) => `<li><b>${esc(tr(label))} · ${esc(paper)}</b><br>${esc(typeof reason === 'string' ? reason : JSON.stringify(reason))}</li>`);
    });
    return items.length ? `<details><summary>${esc(tr('runDetails'))}</summary><ul style="overflow-wrap:anywhere">${items.join('')}</ul></details>` : '';
  }

  async function runsPage(ov) {
    const d = await api('/api/runs');
    const today = new Date();
    const days = [...Array(8)].map((_, i) => { const x = new Date(today); x.setDate(today.getDate() - 7 + i); return x; });
    const key = (x) => `${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())}`;
    const start = new Date(days[0]);
    start.setHours(0, 0, 0, 0);
    const recent = d.runs.filter((r) => new Date(r.started_at) >= start);
    const lanes = [...new Set([...ov.topics.map((t) => t.id), ...recent.map((r) => r.topic_id || '—')])];
    const mark = (r) => `<span class="mark${r.job === 'digest' ? ' bar' : ''}${r.status === 'failed' ? ' failed' : r.status === 'running' ? ' running' : ''}" title="${esc(`${r.job} · ${r.status} · ${when(r.started_at)}`)}"></span>`;
    const head = `<div class="lane days"><span></span>${days.map((x, i) => `<span style="${i === 7 ? 'color:var(--text)' : ''}">${esc(x.toLocaleDateString(locale(), { weekday: 'short', day: 'numeric' }))}</span>`).join('')}</div>`;
    const laneRows = lanes.map((id) => `<div class="lane"><span>${esc(id)}</span>${days.map((x, i) => {
      const marks = recent.filter((r) => (r.topic_id || '—') === id && key(new Date(r.started_at)) === key(x)).reverse().map(mark).join('');
      return `<span class="cell"${i === 7 ? ' style="background:var(--accent-soft)"' : ''}>${marks}</span>`;
    }).join('')}</div>`).join('');
    const runs = d.runs.slice(0, 40).map((r) => `<div class="rrow"><span class="when">${esc(when(r.started_at))}</span><span class="chip ${runChip(r.status)}">${esc(r.status)}</span>
        <div style="min-width:0"><span class="mono">${esc(r.topic_id || '—')}</span> <span class="muted">· ${esc(r.job)}</span><span class="note${r.status === 'failed' ? ' bad' : ''}">${esc(r.status === 'failed' ? r.error : statsLine(r.stats))}</span>${runDetails(r.stats)}</div>
        <span class="dur">${esc(dur(r.started_at, r.finished_at))}</span></div>`).join('') || `<div class="empty">${esc(tr('noRuns'))}</div>`;
    const pmax = Math.max(1, ...d.purposes.map((p) => p.tokens));
    const total = d.purposes.reduce((a, p) => a + p.tokens, 0);
    const purposes = d.purposes.map((p) => `<div class="pbar"><span><span>${esc(p.purpose)} <span class="muted">· ${num(p.calls)} ${esc(tr('calls'))}</span></span><span class="mono">${compact(p.tokens)}</span></span><span class="track"><i style="width:${((p.tokens / pmax) * 100).toFixed(1)}%"></i></span></div>`).join('') || `<div class="empty">${esc(tr('noCalls'))}</div>`;
    const calls = d.calls.map((c) => `<details class="call" data-id="${esc(c.id)}"><summary><span><span class="mono muted" style="font-size:12px">${esc(when(c.created_at))}</span> ${esc(c.purpose)} <span class="muted">· ${esc(c.topic_id || '—')}</span></span><span class="mono" style="font-size:12px">${compact(c.tokens_in || 0)} → ${compact(c.tokens_out || 0)}</span></summary><div class="body"><span class="muted">${esc(tr('loading'))}</span></div></details>`).join('') || `<div class="empty">${esc(tr('noCalls'))}</div>`;
    return {
      main: `<header class="head"><div style="display:flex;flex-direction:column;gap:6px"><div class="eyebrow">${esc(tr('runsEyebrow'))}</div><h1 class="title" style="font-size:clamp(26px,3vw,38px)">${esc(tr('runs'))}</h1></div>
          <div class="legend"><span><span class="mark"></span>${esc(tr('legendJobs'))}</span><span><span class="mark bar"></span>${esc(tr('legendDigest'))}</span><span><span class="mark failed"></span>${esc(tr('failed'))}</span></div></header>
        <section class="card" aria-label="${esc(tr('lanesTitle'))}"><h2>${esc(tr('lanesTitle'))}</h2><div class="lanes"><div class="inner">${head}${laneRows}</div></div></section>
        <div class="row"><section class="card" style="flex:999 1 520px" aria-labelledby="runs-h"><h2 id="runs-h">${esc(tr('recentRuns'))}</h2><div>${runs}</div></section>
          <section style="flex:1 1 340px;min-width:0;display:flex;flex-direction:column;gap:20px"><div class="card"><div class="card-head"><h2>${esc(tr('tokensBy'))}</h2><span class="mono">${compact(total)}</span></div>${purposes}</div>
            <div class="card" style="gap:0"><h2 style="margin-bottom:8px">${esc(tr('latestCalls'))}</h2>${calls}</div></section></div>`,
    };
  }

  // ---------- manual report email ----------
  let mailDraft = null;
  let mailBusy = false;
  let mailResult = null;
  let mailError = '';
  function mailForm() {
    const d = mailDraft;
    if (!d) return '';
    const finished = ['accepted', 'unknown'].includes(mailResult?.state);
    return `<form id="mail-form" class="card settings-form no-print" aria-labelledby="mail-heading"><fieldset${mailBusy ? ' disabled' : ''}>
      <h2 id="mail-heading">${esc(tr(d.test ? 'mailTest' : 'mailOpen'))}</h2>
      ${!d.test ? `<p>${esc(tr('topic'))}: <b>${esc(d.topic)}</b> · ${esc(tr('mailPeriod'))}: <b>${esc(d.period)}</b></p>` : ''}
      <p>${esc(tr('mailSubject'))}: <b>${esc(d.subject)}</b></p><p>${esc(tr('mailSender'))}: ${esc(d.sender || tr('off'))}</p>
      <div class="setting-field"><label for="mail-recipients">${esc(tr('mailRecipients'))}</label><input id="mail-recipients" type="text" value="${esc(d.recipients)}" maxlength="4096" autocomplete="off" aria-describedby="mail-hint"${finished ? ' disabled' : ''}><p id="mail-hint" class="setting-hint">${esc(tr('mailRecipientsHint'))} ${esc(tr('mailNote'))}</p></div>
      ${!d.configured ? `<p class="error">${esc(d.message || tr('off'))}</p>` : ''}
      <div class="settings-actions"><button type="submit" id="mail-send" class="btn primary"${!d.configured || finished ? ' disabled' : ''}>${esc(tr(mailBusy ? 'mailSending' : 'mailSend'))}</button><button type="button" class="btn" data-act="mailClose">${esc(tr('mailCancel'))}</button></div>
      <p id="mail-status" role="status" aria-live="polite"${mailError || mailResult?.state === 'failed' ? ' class="error"' : ''}>${esc(mailError || (mailBusy ? tr('mailSending') : mailResult ? tr({ accepted: 'mailAccepted', failed: 'mailFailed', unknown: 'mailUnknown' }[mailResult.state] || 'mailUnknown') : ''))}${mailResult?.state === 'failed' ? ` ${esc(mailResult.message)}` : ''}${mailResult?.request_id ? ` ${esc(tr('mailRequest'))}: ${esc(mailResult.request_id)}` : ''}</p>
      </fieldset></form>`;
  }
  async function openMail(button, test = false) {
    if (mailBusy) return;
    mailBusy = true; mailResult = null; mailError = '';
    mailDraft = { test, topic: button.dataset.topic || '', period: button.dataset.period || '', recipients: '', configured: false };
    mailDraft.subject = test ? 'ISBE · 邮件配置测试 / Email test' : `ISBE · ${mailDraft.topic} · ${mailDraft.period}`;
    await render();
    try {
      const response = await http('/api/mail');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || tr('mailFailed'));
      Object.assign(mailDraft, data.mail, { recipients: data.recipients || '', csrf: data.csrf_token });
    } catch (error) { mailError = error.message; }
    finally { mailBusy = false; await render(); $('#mail-recipients')?.focus(); }
  }
  async function sendMail(e) {
    e.preventDefault();
    if (mailBusy || !mailDraft?.configured || ['accepted', 'unknown'].includes(mailResult?.state)) return;
    const recipients = [...new Map(mailDraft.recipients.split(/[,;\s]+/).filter(Boolean).map((v) => [v.toLowerCase(), v])).values()].join(', ');
    if (!recipients) { mailError = tr('mailRequired'); await render(); return; }
    const d = { ...mailDraft, recipients };
    const confirmation = `${tr('mailConfirm')}\n${!d.test ? `${tr('topic')}: ${d.topic}\n${tr('mailPeriod')}: ${d.period}\n` : ''}${tr('mailSubject')}: ${d.subject}\n${tr('mailRecipients')}: ${recipients}`;
    if (!window.confirm(confirmation)) return;
    mailDraft.recipients = recipients; mailBusy = true; mailError = ''; mailResult = null;
    await render();
    try {
      const response = await http(`/api/mail/${d.test ? 'test' : 'report'}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': d.csrf }, body: JSON.stringify(d.test ? { recipients } : { topic: d.topic, period: d.period, recipients }) });
      const result = await response.json();
      if (!response.ok) { mailResult = { state: response.status >= 500 ? 'unknown' : 'failed', message: result.error || tr('mailFailed') }; }
      else mailResult = ['accepted', 'failed', 'unknown'].includes(result.state) ? result : { state: 'unknown' };
    } catch { mailResult = { state: 'unknown' }; }
    finally { mailBusy = false; await render(); $('#mail-status')?.focus(); }
  }

  // ---------- service settings ----------
  const PROVIDER_KEYS = { anthropic: 'ANTHROPIC_API_KEY', deepseek: 'DEEPSEEK_API_KEY', openai: 'OPENAI_API_KEY' };
  const SECRET_SETTINGS = new Set([...Object.values(PROVIDER_KEYS), 'GITHUB_TOKEN', 'ZEPTOMAIL_TOKEN', 'SMTP_PASSWORD']);
  let settingsSnapshot = null;
  let settingsEdits = {};
  let settingsSaving = false;
  let settingsNotice = '';
  let settingsError = '';
  let settingsAdvanced = false;
  let settingsSourcesAdvanced = false;
  const settingsDirty = () => Object.keys(settingsEdits).length > 0;
  const settingsValue = (key) => {
    if (key in settingsEdits && settingsEdits[key] !== null) return settingsEdits[key];
    const provider = settingsEdits.ISBE_LLM_PROVIDER || settingsSnapshot.values.ISBE_LLM_PROVIDER;
    const defaults = settingsSnapshot.provider_defaults?.[provider];
    if ((settingsEdits[key] === null || (settingsSnapshot.sources[key] || 'default') === 'default') && defaults && key in defaults) return defaults[key];
    return key in settingsEdits ? '' : settingsSnapshot.values[key] ?? '';
  };

  function settingsField(key, label, type = 'text') {
    const source = settingsSnapshot.sources[key] || 'default';
    const secret = SECRET_SETTINGS.has(key);
    const removing = secret && settingsEdits[key] === null;
    const locked = source === 'environment';
    const value = secret ? settingsEdits[key] ?? '' : settingsValue(key);
    const attrs = `id="setting-${key}" name="${key}" data-setting="${key}" aria-describedby="hint-${key}"${locked || removing ? ' disabled' : ''}`;
    const control = type === 'select'
      ? `<select ${attrs}>${Object.entries(key === 'ISBE_ARTICLE_DNS' ? { system:'articleDnsSystem', cloudflare:'articleDnsCloudflare' } : key === 'EMAIL_TRANSPORT' ? { smtp: 'smtp', zeptomail_api: 'zeptomail_api' } : key === 'SMTP_SECURITY' ? { tls: 'tls', starttls: 'starttls' } : key === 'ISBE_LLM_NO_AUTH' ? { false: 'authRequired', true: 'authNone' } : key === 'ISBE_FULLTEXT_EXTRACTOR' ? { llm: 'extractorLlm', metrail: 'extractorMetrail', off: 'extractorOff' } : { anthropic: 'Anthropic', deepseek: 'DeepSeek', openai: 'OpenAI / compatible' }).map(([v, name]) => `<option value="${v}"${value === v ? ' selected' : ''}>${esc(tr(name))}</option>`).join('')}</select>`
      : type === 'textarea'
        ? `<textarea ${attrs} rows="4" spellcheck="false" placeholder="{}">${esc(value)}</textarea>`
        : `<input ${attrs} type="${secret ? 'password' : type}" value="${esc(value)}"${type === 'number' ? ' min="1" step="1"' : ''} ${secret ? `autocomplete="new-password" placeholder="${esc(tr(settingsSnapshot.configured[key] ? 'keepSecret' : 'newSecret'))}"` : 'autocomplete="off"'} spellcheck="false">`;
    const sourceLabel = { environment: 'sourceEnvironment', file: 'sourceFile', default: 'sourceDefault' }[source] || 'sourceDefault';
    const hint = [locked ? tr('environmentLock') : removing ? tr('removingSecret') : secret ? tr(settingsSnapshot.configured[key] ? 'keepSecret' : 'newSecret') : '', key === 'METRAIL_API_URL' ? tr('metrailRequired') : key === 'ISBE_ARTICLE_DNS' ? tr('articleDnsNote') : ''].filter(Boolean).join(' ');
    return `<div class="setting-field${type === 'textarea' ? ' wide' : ''}"><div class="setting-label"><label for="setting-${key}">${esc(tr(label))}</label><span class="chip plain">${esc(tr(sourceLabel))}${secret ? ` · ${esc(tr(settingsSnapshot.configured[key] ? 'online' : 'off'))}` : ''}</span></div>${control}<div class="setting-hint" id="hint-${key}">${esc(hint)}</div>${secret && !locked && (settingsSnapshot.configured[key] || removing) ? `<button type="button" class="setting-remove" data-act="removeSecret" data-key="${key}">${esc(tr(removing ? 'undoRemove' : 'removeSecret'))}</button>` : ''}</div>`;
  }

  async function settingsPage() {
    if (!settingsSnapshot) settingsSnapshot = await api('/api/settings');
    if (!settingsSnapshot) throw new Error(tr('settingsUnavailable'));
    const extractor = settingsValue('ISBE_FULLTEXT_EXTRACTOR');
    return { main: `<header class="head"><div class="settings-intro"><div class="eyebrow">ISBE · ${esc(tr('settings'))}</div><h1 class="title">${esc(tr('settings'))}</h1><p class="sub">${esc(tr('settingsNote'))}</p></div><div class="settings-actions"><button type="button" class="btn" data-act="guideOpen">${esc(tr('guideOpen'))}</button><a class="btn" data-link href="/topics">${esc(tr('topicSettings'))} →</a></div></header>
      <form id="settings-form" class="settings-form"><fieldset${settingsSaving ? ' disabled' : ''}>
        <section class="card settings-section" aria-labelledby="llm-settings-h"><div class="settings-section-head"><span class="settings-index mono" aria-hidden="true">01</span><div><h2 id="llm-settings-h">${esc(tr('llmSettings'))}</h2><p>${esc(tr('llmSettingsNote'))}</p></div></div>
          <div class="settings-grid">${settingsField('ISBE_LLM_PROVIDER', 'provider', 'select')}${settingsField('ISBE_LLM_BASE_URL', 'baseUrl', 'url')}${settingsField(PROVIDER_KEYS[settingsValue('ISBE_LLM_PROVIDER')], 'API key')}</div>
          ${settingsValue('ISBE_LLM_PROVIDER') === 'openai' ? `<div class="settings-grid">${settingsField('ISBE_LLM_NO_AUTH', 'noAuth', 'select')}</div><p class="settings-storage">${esc(tr('noAuthNote'))}</p>` : ''}<p class="settings-storage">${esc(tr('providerDefaultsNote'))}</p><p id="llm-readiness" role="status">${esc(tr(settingsDirty() ? 'llmUnsaved' : settingsSnapshot.llm?.ready ? 'llmReady' : 'llmIncomplete'))}${!settingsDirty() && settingsSnapshot.llm?.error ? `: ${esc(settingsSnapshot.llm.error)}` : ''}</p>
          <details id="settings-advanced"${settingsAdvanced ? ' open' : ''}><summary>${esc(tr('advanced'))}</summary><div class="settings-grid">${settingsField('ISBE_LLM_MODEL_FAST', 'fastModel')}${settingsField('ISBE_LLM_MODEL_SMART', 'smartModel')}${settingsField('ISBE_LLM_MAX_TOKENS', 'maxTokens', 'number')}${settingsField('ISBE_LLM_TIMEOUT_S', 'timeout', 'number')}${settingsField('ISBE_LLM_EXTRA_BODY', 'extraBody', 'textarea')}</div></details>
        </section>
        <section class="card settings-section" aria-labelledby="fulltext-settings-h"><div class="settings-section-head"><span class="settings-index mono" aria-hidden="true">02</span><div><h2 id="fulltext-settings-h">${esc(tr('fulltextSettings'))}</h2></div></div><div class="settings-grid">${settingsField('ISBE_FULLTEXT_EXTRACTOR', 'extractor', 'select')}${extractor === 'metrail' ? settingsField('METRAIL_API_URL', 'metrailUrl', 'url') : ''}</div><p class="settings-storage">${esc(tr({ llm: 'extractorLlmNote', metrail: 'extractorMetrailNote', off: 'extractorOffNote' }[extractor] || 'extractorLlmNote'))}</p>${extractor === 'llm' ? `<p class="settings-storage">${esc(tr('extractorLlmFigures'))}</p>` : ''}${settingsSnapshot.sources.ISBE_FULLTEXT_EXTRACTOR === 'default' && !('ISBE_FULLTEXT_EXTRACTOR' in settingsEdits) ? `<p class="settings-storage">${esc(tr('extractorDefaultNote'))}</p>` : ''}</section>
        <section class="card settings-section" aria-labelledby="sources-settings-h"><div class="settings-section-head"><span class="settings-index mono" aria-hidden="true">03</span><div><h2 id="sources-settings-h">${esc(tr('sourceSettings'))}</h2><p>${esc(tr('sourceSettingsNote'))}</p></div></div><div class="settings-grid">${settingsField('ESSAIES_URL', 'essaiesUrl', 'url')}${settingsField('GITHUB_TOKEN', 'githubToken')}</div><details id="settings-sources-advanced"${settingsSourcesAdvanced ? ' open' : ''}><summary>${esc(tr('sourceAdvanced'))}</summary><div class="settings-grid">${settingsField('ARXIV_API_URL', 'arxivUrl', 'url')}${settingsField('GITHUB_API_URL', 'githubUrl', 'url')}</div></details><h3>${esc(tr('webSearch'))}</h3><p class="settings-storage">${esc(tr('webSearchSourceNote'))}</p><div class="settings-grid">${settingsField('SEARXNG_URL', 'webSearchSearxng', 'url')}${settingsField('ISBE_ARTICLE_DNS', 'articleDns', 'select')}</div></section>
        <section class="card settings-section" aria-labelledby="mail-settings-h"><h2 id="mail-settings-h">${esc(tr('mailSettings'))}</h2><p>${esc(tr('mailNote'))}</p><p>${esc(tr(settingsSnapshot.mail?.configured ? 'online' : 'off'))}${settingsSnapshot.mail?.sender ? ` · ${esc(settingsSnapshot.mail.sender)}` : ''}</p>${settingsSnapshot.mail?.message ? `<p class="error">${esc(settingsSnapshot.mail.message)}</p>` : ''}<div class="settings-grid">${settingsField('EMAIL_TRANSPORT', 'mailTransport', 'select')}${(settingsValue('EMAIL_TRANSPORT') || 'smtp') === 'smtp' ? `${settingsField('SMTP_HOST', 'mailHost')}${settingsField('SMTP_PORT', 'mailPort', 'number')}${settingsField('SMTP_SECURITY', 'mailSecurity', 'select')}${settingsField('SMTP_USER', 'mailUser')}${settingsField('SMTP_PASSWORD', 'mailPassword')}` : `${settingsField('ZEPTOMAIL_API_URL', 'mailEndpoint', 'url')}${settingsField('ZEPTOMAIL_TOKEN', 'mailToken')}`}${settingsField('SMTP_FROM', 'mailSender', 'email')}${settingsField('SMTP_FROM_NAME', 'mailSenderName')}${settingsField('ISBE_EMAIL_DEFAULT_RECIPIENTS', 'mailDefault')}</div>${(settingsValue('EMAIL_TRANSPORT') || 'smtp') === 'smtp' ? `<p class="settings-storage">${esc(tr('mailSmtpNote'))}</p>` : ''}<button type="button" class="btn" data-act="mailTest"${mailBusy ? ' disabled' : ''}>${esc(tr('mailTest'))}</button></section>
        <p class="settings-storage">${esc(tr('settingsStorage'))}</p>
        <div class="settings-actions"><button type="submit" class="btn primary" id="settings-save"${!settingsDirty() ? ' disabled' : ''}>${esc(tr(settingsSaving ? 'savingSettings' : 'saveSettings'))}</button><button type="button" class="btn" id="settings-discard" data-act="discardSettings"${!settingsDirty() ? ' disabled' : ''}>${esc(tr('discardSettings'))}</button><p id="settings-status" role="status" aria-live="polite"${settingsError ? ' class="error"' : ''}>${esc(settingsError || tr(settingsSaving ? 'savingSettings' : settingsNotice || (settingsDirty() ? 'unsavedSettings' : '')))}</p></div>
      </fieldset></form>${mailDraft?.test ? mailForm() : ''}` };
  }

  function editSetting(el) {
    const key = el.dataset.setting;
    if (!key || !settingsSnapshot || settingsSaving || settingsSnapshot.sources[key] === 'environment') return;
    const value = el.value;
    // An explicit method must stay fixed when a hidden metrail URL changes its inferred default.
    const explicitDefault = (settingsSnapshot.sources[key] || 'default') === 'default' && (key === 'ISBE_FULLTEXT_EXTRACTOR' || ['ISBE_LLM_BASE_URL', 'ISBE_LLM_MODEL_FAST', 'ISBE_LLM_MODEL_SMART'].includes(key));
    if (SECRET_SETTINGS.has(key) ? value === '' : !explicitDefault && value === (settingsSnapshot.values[key] ?? '')) delete settingsEdits[key];
    else settingsEdits[key] = value || null;
    settingsNotice = '';
    settingsError = '';
    const status = $('#settings-status');
    if (status) { status.textContent = settingsDirty() ? tr('unsavedSettings') : ''; status.className = ''; }
    if ($('#llm-readiness')) $('#llm-readiness').textContent = tr('llmUnsaved');
    for (const id of ['#settings-save', '#settings-discard']) if ($(id)) $(id).disabled = !settingsDirty();
  }

  async function saveSettings(e) {
    if (e.target.id !== 'settings-form') return;
    e.preventDefault();
    if (settingsSaving || !settingsDirty()) return;
    const extra = settingsEdits.ISBE_LLM_EXTRA_BODY;
    if (extra) {
      try { const v = JSON.parse(extra); if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error(); }
      catch { settingsError = tr('invalidExtraBody'); settingsAdvanced = true; await render(); $('#setting-ISBE_LLM_EXTRA_BODY')?.focus(); return; }
    }
    settingsSaving = true;
    settingsError = '';
    await render();
    try {
      const r = await http('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': settingsSnapshot.csrf_token }, body: JSON.stringify(settingsEdits) });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || String(r.status));
      settingsSnapshot = data;
      settingsEdits = {};
      settingsNotice = 'savedSettings';
      memo.delete('/api/settings');
      memo.delete('/api/overview');
    } catch (err) { settingsError = err.message; }
    finally { settingsSaving = false; await render(); $('#settings-status')?.scrollIntoView({ block: 'nearest' }); }
  }

  // ---------- topic configuration ----------
  const TOPIC_LISTS = new Set(['arxiv.categories', 'arxiv.include_keywords', 'github.repos', 'retrieval.in_scope', 'retrieval.out_of_scope', 'retrieval.out_of_scope_keywords', 'retrieval.require_any']);
  const TOPIC_NUMBERS = new Set(['window_days', 'arxiv.max_results', 'digest.max_papers']);
  const TOPIC_DEFAULTS = { lang: 'en', cadence: 'weekly', active: true, window_days: 7, 'arxiv.max_results': 50, 'digest.max_papers': 25 };
  const clone = (v) => JSON.parse(JSON.stringify(v));
  let topicsSnapshot = null;
  let topicDetail = null;
  let topicOriginal = null;
  let topicEdits = {};
  let topicSearch = '';
  let topicBusy = false;
  let topicError = '';
  let topicNotice = '';
  let topicConflict = false;
  let topicRulesOpen = false;
  let topicRequest = 0;
  const topicDirty = () => !!topicDetail && (topicDetail.id === null || Object.keys(topicEdits).length > 0);
  const topicInitial = (path) => path === 'arxivEnabled' ? topicOriginal?.arxiv != null : path.split('.').reduce((v, key) => v?.[key], topicOriginal) ?? TOPIC_DEFAULTS[path] ?? '';
  const topicValue = (path) => {
    const value = path in topicEdits ? topicEdits[path] : topicInitial(path);
    return TOPIC_LISTS.has(path) && Array.isArray(value) ? value.join('\n') : value;
  };
  const topicFeeds = () => {
    const value = topicValue('rss.feeds');
    return Array.isArray(value) ? value : [];
  };
  const topicStatus = () => topicConflict ? tr('topicConflict') : topicError || (topicBusy ? tr('loading') : topicNotice ? tr(topicNotice) : topicDirty() ? tr('topicUnsaved') : '');

  async function topicApi(url, method = 'GET', body) {
    const r = await http(url, method === 'GET' ? undefined : { method, headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': topicsSnapshot.csrf_token }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { const error = new Error(data.error || String(r.status)); error.status = r.status; throw error; }
    return data;
  }

  function topicListHtml() {
    const all = topicsSnapshot.topics;
    const query = topicSearch.trim().toLocaleLowerCase();
    const topics = all.filter((t) => `${t.id} ${t.label || ''}`.toLocaleLowerCase().includes(query));
    if (!topics.length) return `<p class="empty">${esc(tr(all.length ? 'topicNoMatch' : 'topicEmpty'))}</p>`;
    return topics.map((t) => `<button type="button" class="topic-choice" data-act="topicSelect" data-id="${esc(t.id)}" aria-pressed="${topicDetail?.id === t.id}"${topicBusy ? ' disabled' : ''}><span class="topic-choice-label">${esc(t.label || t.id)}</span><span class="mono muted">${esc(t.id)}</span><span class="chip ${t.error ? 'danger' : t.active === false ? 'plain' : ''}">${esc(tr(t.error ? 'topicInvalid' : t.active === false ? 'inactive' : 'healthy'))}</span>${t.error ? `<small class="error">${esc(t.error)}</small>` : ''}</button>`).join('');
  }

  function topicField(path, label, type = 'text', hint = '') {
    const value = path === 'id' && topicDetail.id !== null ? topicDetail.id : topicValue(path);
    const id = `topic-${path.replaceAll('.', '-')}`;
    const attrs = `id="${id}" data-topic="${path}"${['arxiv.categories', 'arxiv.include_keywords'].includes(path) ? ' required aria-required="true"' : ''} aria-describedby="${id}-hint"${path === 'id' && topicDetail.id !== null ? ' disabled' : ''}`;
    const control = type === 'select'
      ? `<select ${attrs}>${Object.entries(path === 'lang' ? { zh: '中文', en: 'English' } : { weekly: tr('weekly'), daily: tr('daily') }).map(([v, name]) => `<option value="${v}"${value === v ? ' selected' : ''}>${esc(name)}</option>`).join('')}</select>`
      : type === 'textarea'
        ? `<textarea ${attrs} rows="3">${esc(value)}</textarea>`
        : `<input ${attrs} type="${type}" value="${esc(value)}"${type === 'number' ? ' min="1" step="1"' : ''} autocomplete="off">`;
    return `<div class="setting-field"><label for="${id}">${esc(tr(label))}</label>${control}<small class="setting-hint" id="${id}-hint">${esc(tr(hint || (TOPIC_LISTS.has(path) ? 'topicLines' : '')))}</small></div>`;
  }

  function topicCheckbox(path, label, hint = '') {
    return `<div class="topic-check"><label><input id="topic-${path}" type="checkbox" data-topic="${path}"${topicValue(path) ? ' checked' : ''}>${esc(tr(label))}</label>${hint ? `<small class="setting-hint">${esc(tr(hint))}</small>` : ''}</div>`;
  }

  function topicForm() {
    if (!topicDetail) return `<div class="card"><p class="empty">${esc(tr('topicChoose'))}</p></div>`;
    const actions = `${topicDetail.id !== null ? `<button type="button" class="btn" data-act="topicReload"${topicBusy ? ' disabled' : ''}>${esc(tr('topicReload'))}</button><button type="button" class="btn topic-delete" data-act="topicDelete"${topicBusy ? ' disabled' : ''}>${esc(tr('topicDelete'))}</button>` : ''}`;
    const error = topicDetail.error ? `<div class="topic-warning" role="alert"><b>${esc(tr('topicInvalid'))}</b><p>${esc(topicDetail.error)}</p></div>` : '';
    if (!topicOriginal) return `<div class="card">${error}<p class="sub">${esc(tr('topicUnreadable'))}</p><div class="settings-actions">${actions}</div></div>`;
    const feeds = topicFeeds().map((feed, i) => `<div class="topic-feed"><div class="setting-field"><label for="topic-feed-${i}-name">${esc(tr('topicFeedName'))} ${i + 1}</label><input id="topic-feed-${i}-name" data-topic-feed="${i}" data-feed-field="name" value="${esc(feed?.name)}" autocomplete="off"></div><div class="setting-field"><label for="topic-feed-${i}-url">${esc(tr('topicFeedUrl'))} ${i + 1}</label><input id="topic-feed-${i}-url" data-topic-feed="${i}" data-feed-field="url" value="${esc(feed?.url)}" type="text" spellcheck="false" autocomplete="off"></div><button type="button" class="btn" data-act="topicRemoveFeed" data-index="${i}" aria-label="${esc(tr('topicRemoveFeed', { n: i + 1 }))}">×</button></div>`).join('');
    return `<form id="topic-form" class="settings-form" novalidate><fieldset${topicBusy ? ' disabled' : ''}>${error}
      <section class="card settings-section"><div class="card-head"><h2>${esc(tr('topicBasic'))}</h2><span class="mono">${esc(topicDetail.id ?? tr('topicNew'))}</span></div><div class="settings-grid">${topicField('id', 'topicId')}${topicField('label', 'topicLabel')}${topicField('lang', 'topicLang', 'select')}${topicField('cadence', 'topicCadence', 'select', 'firstSchedule')}${topicField('window_days', 'topicWindow', 'number')}${topicField('digest.max_papers', 'topicMaxPapers', 'number')}</div>${topicCheckbox('active', 'topicActive', 'topicBatchNote')}</section>
      <section class="card settings-section"><h2>${esc(tr('topicSources'))}</h2>${topicCheckbox('arxivEnabled', 'topicArxiv')}${topicValue('arxivEnabled') ? `<div class="settings-grid">${topicField('arxiv.categories', 'topicCategories', 'textarea', 'topicCategoryHelp')}${topicField('arxiv.include_keywords', 'topicKeywords', 'textarea', 'topicKeywordHelp')}${topicField('arxiv.max_results', 'topicMaxResults', 'number')}</div><p class="setting-hint">${esc(tr('topicQueryHelp'))} <a href="https://arxiv.org/category_taxonomy" target="_blank" rel="noopener">${esc(tr('topicTaxonomy'))} ↗</a></p>` : ''}${isAdmin() ? `${topicField('github.repos', 'topicRepos', 'textarea', 'topicRepoNote')}<div class="topic-feeds"><h3>${esc(tr('topicFeeds'))}</h3><p class="setting-hint">${esc(tr('topicFeedNote'))}</p>${feeds}<button type="button" class="btn" data-act="topicAddFeed">${esc(tr('topicAddFeed'))}</button></div>` : ''}</section>
      <section class="card settings-section"><details id="topic-rules"${topicRulesOpen ? ' open' : ''}><summary>${esc(tr('topicRules'))}</summary><div class="settings-grid">${topicField('retrieval.intent', 'topicIntent', 'textarea', 'topicIntentNote')}${topicField('retrieval.quality_bar', 'topicQuality', 'textarea')}${topicField('retrieval.in_scope', 'topicInScope', 'textarea')}${topicField('retrieval.out_of_scope', 'topicOutScope', 'textarea')}${topicField('retrieval.out_of_scope_keywords', 'topicExcluded', 'textarea')}${topicField('retrieval.require_any', 'topicRequired', 'textarea')}</div></details></section>
      <p class="settings-storage">${esc(tr('topicAdvancedKept'))}</p><div class="settings-actions"><button type="submit" class="btn primary" id="topic-save"${topicDirty() ? '' : ' disabled'}>${esc(tr(topicBusy ? 'topicSaving' : 'topicSave'))}</button><button type="button" class="btn" id="topic-discard" data-act="topicDiscard"${topicDirty() ? '' : ' disabled'}>${esc(tr('discardSettings'))}</button>${actions}</div></fieldset></form>`;
  }

  async function topicsPage() {
    if (!topicsSnapshot) topicsSnapshot = await api('/api/topics');
    if (!topicsSnapshot) throw new Error(tr('topicMissing'));
    return { main: `<header class="head"><div class="settings-intro"><div class="eyebrow">ISBE · ${esc(tr('topicSettings'))}</div><h1 class="title">${esc(tr('topicSettings'))}</h1><p class="sub">${esc(tr(isAdmin() ? 'topicSettingsNote' : 'topicOwnNote'))}</p></div><div class="settings-actions"><button type="button" class="btn" data-act="guideOpen" data-topic="${esc(topicDetail?.id || '')}">${esc(tr('guideOpen'))}</button>${isAdmin() ? `<a class="btn" data-link href="/settings">${esc(tr('settings'))} →</a>` : ''}</div></header>
      <div class="topics-editor"><aside class="card topic-sidebar"><button type="button" class="btn primary" data-act="topicNew"${topicBusy ? ' disabled' : ''}>+ ${esc(tr('topicNew'))}</button><div class="setting-field"><label for="topic-search">${esc(tr('topicSearch'))}</label><input type="search" id="topic-search" data-input="topicSearch" value="${esc(topicSearch)}" autocomplete="off"></div><div id="topic-list" class="topic-list">${topicListHtml()}</div></aside><div class="topic-editor"><p id="topic-status" class="${topicError || topicConflict ? 'error' : 'topic-status'}" role="status" aria-live="polite" tabindex="-1">${esc(topicStatus())}</p>${topicForm()}${topicNotice === 'topicSaved' && topicDetail?.id ? firstReportGuide(topicDetail.id) : ''}</div></div>` };
  }

  function updateTopicStatus() {
    topicError = ''; topicNotice = '';
    const status = $('#topic-status');
    if (status) { status.textContent = topicStatus(); status.className = topicConflict ? 'error' : 'topic-status'; }
    if ($('#topic-save')) $('#topic-save').disabled = !topicDirty();
    if ($('#topic-discard')) $('#topic-discard').disabled = !topicDirty();
  }

  function editTopic(el) {
    if (!topicOriginal || topicBusy) return;
    if (el.dataset.topicFeed !== undefined) {
      const feeds = clone(topicFeeds());
      const index = Number(el.dataset.topicFeed);
      if (!feeds[index] || !['name', 'url'].includes(el.dataset.feedField)) return;
      feeds[index] = { ...feeds[index], [el.dataset.feedField]: el.value };
      topicEdits['rss.feeds'] = feeds;
    } else {
      const path = el.dataset.topic;
      if (!path || (path === 'id' && topicDetail.id !== null)) return;
      const value = el.type === 'checkbox' ? el.checked : el.value;
      const initial = topicInitial(path);
      const same = el.type === 'checkbox' ? value === initial : value === String(Array.isArray(initial) ? initial.join('\n') : initial);
      if (same) delete topicEdits[path]; else topicEdits[path] = value;
    }
    updateTopicStatus();
  }

  function topicConfig() {
    const config = clone(topicOriginal);
    const arxiv = !!topicValue('arxivEnabled');
    if (arxiv && !config.arxiv) config.arxiv = { categories: [], include_keywords: [], max_results: 50 };
    for (const [path, raw] of Object.entries(topicEdits)) {
      if (path === 'arxivEnabled' || (path.startsWith('arxiv.') && !arxiv)) continue;
      const value = TOPIC_LISTS.has(path) ? raw.split(/\r?\n/).map((v) => v.trim()).filter(Boolean) : TOPIC_NUMBERS.has(path) ? Number(raw) : raw;
      const parts = path.split('.');
      if (parts.length === 2 && !config[parts[0]] && (value === '' || (Array.isArray(value) && !value.length))) continue;
      let object = config;
      for (const part of parts.slice(0, -1)) {
        if (!object[part] || typeof object[part] !== 'object' || Array.isArray(object[part])) object[part] = {};
        object = object[part];
      }
      object[parts.at(-1)] = value;
    }
    if (!arxiv) delete config.arxiv;
    if (topicDetail.id !== null) config.id = topicDetail.id;
    return config;
  }

  function validateTopic(config) {
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(config.id || '')) return ['topicBadId', 'id'];
    if (typeof config.label !== 'string' || !config.label.trim()) return ['topicNeedLabel', 'label'];
    for (const path of TOPIC_NUMBERS) {
      if (path.startsWith('arxiv.') && !config.arxiv) continue;
      const value = path.split('.').reduce((v, k) => v?.[k], config) ?? TOPIC_DEFAULTS[path];
      if (!Number.isSafeInteger(value) || value <= 0) return ['topicBadNumber', path];
    }
    if (config.arxiv && (!Array.isArray(config.arxiv.categories) || !config.arxiv.categories.some((v) => String(v).trim()))) return ['topicNeedCategories', 'arxiv.categories'];
    if (config.arxiv && (!Array.isArray(config.arxiv.include_keywords) || !config.arxiv.include_keywords.some((v) => String(v).trim()))) return ['topicNeedKeywords', 'arxiv.include_keywords'];
    const feeds = config.rss?.feeds || [];
    if (!config.arxiv && !feeds.length && !config.zhihu) return ['topicNeedSource', 'arxivEnabled'];
    if (!Array.isArray(feeds) || feeds.some((f) => typeof f?.name !== 'string' || !f.name.trim() || typeof f?.url !== 'string' || !f.url.trim())) return ['topicBadFeed', 'rss.feeds'];
    if (config.retrieval && (typeof config.retrieval.intent !== 'string' || !config.retrieval.intent.trim())) return ['topicNeedIntent', 'retrieval.intent'];
    if (config.github?.repos && (!Array.isArray(config.github.repos) || config.github.repos.some((v) => !/^[^/\s]+\/[^/\s]+$/.test(v)))) return ['topicBadRepo', 'github.repos'];
    return null;
  }

  function acceptTopic(detail) {
    topicDetail = detail;
    topicOriginal = detail.config && typeof detail.config === 'object' && !Array.isArray(detail.config) ? clone(detail.config) : null;
    topicEdits = {}; topicError = ''; topicNotice = ''; topicConflict = false;
    topicRulesOpen = !!topicOriginal?.retrieval;
  }

  async function selectTopic(id, reload = false) {
    if (topicBusy || (!reload && topicDetail?.id === id)) return;
    if (topicDirty() && !window.confirm(tr('topicLeave'))) return;
    const request = ++topicRequest;
    topicBusy = true; topicError = ''; topicNotice = '';
    await render();
    try { const detail = await topicApi(`/api/topics/${enc(id)}`); if (request === topicRequest) acceptTopic(detail); }
    catch (err) { if (request === topicRequest) topicError = err.message; }
    finally { if (request === topicRequest) { topicBusy = false; await render(); } }
  }

  function newTopic() {
    if (topicBusy || (topicDirty() && !window.confirm(tr('topicLeave')))) return;
    acceptTopic({ id: null, revision: null, config: { id: '', label: '', lang: prefs.lang, cadence: 'weekly', active: true, window_days: 7, arxiv: { categories: [], include_keywords: [], max_results: 50 }, digest: { max_papers: 25 } } });
    render().then(() => $('#topic-id')?.focus());
  }

  async function saveTopic(e) {
    e.preventDefault();
    if (!topicOriginal || topicBusy || !topicDirty()) return;
    const config = topicConfig();
    const invalid = validateTopic(config);
    if (invalid) { topicError = tr(invalid[0]); topicConflict = false; if (invalid[1].startsWith('retrieval.')) topicRulesOpen = true; await render(); $(`#topic-${invalid[1].replaceAll('.', '-')}`)?.focus(); return; }
    const creating = topicDetail.id === null;
    let createdId = '';
    topicBusy = true; topicError = ''; topicNotice = ''; topicConflict = false;
    await render();
    try {
      const detail = await topicApi(creating ? '/api/topics' : `/api/topics/${enc(topicDetail.id)}`, creating ? 'POST' : 'PUT', creating ? { config } : { config, revision: topicDetail.revision });
      acceptTopic(detail);
      topicsSnapshot.topics = topicsSnapshot.topics.filter((t) => t.id !== detail.id).concat({ id: detail.id, label: detail.config.label, active: detail.config.active !== false, revision: detail.revision, error: detail.error }).sort((a, b) => a.id.localeCompare(b.id));
      memo.clear(); topicNotice = 'topicSaved';
      if (creating) createdId = detail.id;
    } catch (err) { topicError = err.message; topicConflict = err.status === 409 && topicDetail.id !== null; }
    finally { topicBusy = false; await render(); $('#topic-status')?.focus(); }
    if (createdId) await openGuide(createdId);
  }

  async function deleteTopic() {
    if (!topicDetail || topicDetail.id === null || topicBusy || !window.confirm(tr('topicDeleteConfirm', { id: topicDetail.id }))) return;
    topicBusy = true; topicError = ''; topicNotice = ''; topicConflict = false;
    await render();
    try {
      await topicApi(`/api/topics/${enc(topicDetail.id)}`, 'DELETE', { revision: topicDetail.revision });
      topicsSnapshot.topics = topicsSnapshot.topics.filter((t) => t.id !== topicDetail.id);
      topicDetail = null; topicOriginal = null; topicEdits = {}; topicNotice = 'topicDeleted'; memo.clear();
    } catch (err) { topicError = err.message; topicConflict = err.status === 409; }
    finally { topicBusy = false; await render(); $('#topic-status')?.focus(); }
  }

  // ---------- customize ----------
  function customPanel() {
    const light = prefs.mode === 'light';
    const fill = (h, c) => (light
      ? `linear-gradient(135deg, oklch(.5 ${0.12 * c} ${h}) 0 50%, oklch(.955 ${0.012 * c} ${h}) 50% 100%)`
      : `linear-gradient(135deg, oklch(.8 ${0.135 * c} ${h}) 0 50%, oklch(.205 ${0.016 * c} ${h}) 50% 100%)`);
    const names = { signal: 'Signal', glacier: 'Glacier', iris: 'Iris', coral: 'Coral', ember: 'Ember', moss: 'Moss', graphite: 'Graphite' };
    const swatches = Object.entries(PRESETS).map(([k, [h, c]]) => `<button type="button" data-act="theme" data-v="${k}" aria-pressed="${prefs.theme === k}"><i style="background:${fill(h, c)}"></i>${names[k]}</button>`).join('')
      + `<button type="button" data-act="theme" data-v="custom" aria-pressed="${prefs.theme === 'custom'}"><i style="background:conic-gradient(oklch(.75 .14 0),oklch(.75 .14 60),oklch(.75 .14 120),oklch(.75 .14 180),oklch(.75 .14 240),oklch(.75 .14 300),oklch(.75 .14 360))"></i>${esc(tr('custom'))}</button>`;
    const modes = [['light', 'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'], ['dark', 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z'], ['device', 'M3 5h18v11H3zM8 20h8M12 16v4']]
      .map(([k, d]) => `<button type="button" data-act="mode" data-v="${k}" aria-pressed="${prefs.mode === k}"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"></path></svg>${esc(tr(k))}</button>`).join('');
    const [h, c] = prefs.theme === 'custom' ? [prefs.hue, 1] : PRESETS[prefs.theme] || PRESETS.signal;
    const papers = [['paper', 'oklch(.982 .008 95)', 'oklch(.21 0 0)', 'oklch(.86 .012 85)'], ['sepia', 'oklch(.94 .035 85)', 'oklch(.25 .03 60)', 'oklch(.80 .04 80)'], ['night', `oklch(.22 ${0.012 * c} ${h})`, 'oklch(.91 .01 90)', `oklch(.36 ${0.015 * c} ${h})`]]
      .map(([k, bg, ink, line]) => `<button type="button" class="paperpick" data-act="paper" data-v="${k}" aria-pressed="${prefs.paper === k}" style="padding:8px 8px 10px;gap:8px;align-items:stretch"><i style="background:${bg}"><b style="display:block;height:6px;width:72%;border-radius:3px;background:${ink}"></b><b style="display:block;height:4px;width:92%;border-radius:2px;background:${line}"></b><b style="display:block;height:4px;width:50%;border-radius:2px;background:oklch(.45 ${0.09 * c} ${h})"></b></i>${esc(tr(k))}</button>`).join('');
    return `<aside class="custom" aria-labelledby="customize-h"><div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px"><div style="display:flex;flex-direction:column;gap:4px"><h2 id="customize-h">${esc(tr('customTitle'))}</h2><span class="muted" style="font-size:13px">${esc(tr('customNote'))}</span></div>
        <button type="button" class="btn" data-act="panel" aria-label="${esc(tr('close'))}" style="width:44px;height:44px;padding:0">${ICON.close}</button></div>
      <section><h3>${esc(tr('mode'))}</h3><div class="opts3">${modes}</div></section>
      <section><h3>${esc(tr('color'))}</h3><div class="swatches">${swatches}</div>
        <label class="hue"><span style="display:flex;justify-content:space-between"><span>${esc(tr('customHue'))}</span><span class="mono muted" id="hue-v">${prefs.hue}°</span></span><span class="rainbow" aria-hidden="true"></span><input type="range" min="0" max="360" step="1" value="${prefs.hue}" data-input="hue"></label></section>
      <section><h3>${esc(tr('surface'))}</h3><div class="opts3">${papers}</div><span class="muted" style="font-size:12.5px;line-height:1.5">${esc(tr('surfaceNote'))}</span></section>
      <button type="button" class="btn" data-act="reset" style="align-self:flex-start">${esc(tr('reset'))}</button></aside>`;
  }

  function applyTheme() {
    const app = $('#app');
    const [h, c] = prefs.theme === 'custom' ? [prefs.hue, 1] : PRESETS[prefs.theme] || PRESETS.signal;
    app.style.setProperty('--h', h);
    app.style.setProperty('--c', c);
    app.dataset.mode = prefs.mode;
    app.dataset.paper = prefs.paper;
    document.documentElement.lang = prefs.lang === 'zh' ? 'zh-CN' : 'en';
    document.body.style.background = getComputedStyle(app).backgroundColor;
  }

  // ---------- website sources and collected material ----------
  let sourcesSnapshot = null, sourceDetail = null, sourceDraft = null, sourceBusy = false, sourceNotice = '', sourceError = '';
  let sourceDiscovery = { url: '', topic: '', auto_enable: true }, sourceDiscoveryDirty = false, sourceImportText = '';
  let sourceJobs = [], sourceMaterials = null, sourceFilter = '', sourceFilterName = '', sourceOffset = 0, sourceLoaded = false;
  let sourceView = 'catalog', sourceToolsOpen = false, sourceImportOpen = false, sourceQuery = { q:'', site:'', topic:'', status:'' }, sourceQueryDraft = { ...sourceQuery }, sourceCatalogOffset = 0, sourceSelection = new Map();
  let sourceReadErrors = {}, sourceTimer = null, sourceRequest = 0, sourceMaterialRequest = 0, sourceCatalogRequest = 0, sourceDetailRequest = 0, sourceCatalogLoading = false;
  // The open article timeline in the material inbox: { id, data } or { id, error }.
  let sourceTimeline = { id:null }, sourceTimelineRequest = 0;
  let searchSnapshot = null, searchDetail = null, searchDraft = null, searchOriginal = null, sourceDateFilter = '', searchReadRequest = 0;
  let mediaWeekly = null, mediaWeeklyTopics = [], mediaWeeklyLoading = false, mediaWeeklyResult = null, mediaWeeklyRequest = 0;
  const searchSame = (plan) => ({ ...plan, region:(plan.region || '').trim(), institution:(plan.institution || '').trim() });
  const searchDirty = () => !!searchDraft && JSON.stringify(searchSame(searchDraft)) !== JSON.stringify(searchSame(searchOriginal));
  const sourceConfigDirty = () => !!sourceDraft && JSON.stringify(sourceDraft) !== JSON.stringify(sourceDetail?.config);
  const sourceDirty = () => sourceConfigDirty() || sourceDiscoveryDirty || !!sourceImportText || searchDirty();
  const sourceRunning = () => sourceJobs.some((job) => job.state === 'running');
  const sourceDate = (value) => value ? (Number.isNaN(new Date(value).getTime()) ? value : new Date(value).toLocaleString(locale())) : tr('sourceNever');
  const sourceLink = (url, label) => safeUrl(url) ? `<a href="${esc(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : esc(label);
  function resetSources() {
    clearTimeout(sourceTimer); ++sourceRequest; ++sourceMaterialRequest; ++sourceCatalogRequest; ++sourceDetailRequest;
    sourcesSnapshot = null; sourceDetail = null; sourceDraft = null; sourceBusy = false; sourceNotice = ''; sourceError = '';
    sourceDiscovery = { url: '', topic: '', auto_enable: true }; sourceDiscoveryDirty = false; sourceImportText = '';
    sourceJobs = []; sourceMaterials = null; sourceFilter = ''; sourceFilterName = ''; sourceOffset = 0; sourceLoaded = false; sourceReadErrors = {};
    sourceTimeline = { id:null }; ++sourceTimelineRequest;
    sourceView = 'catalog'; sourceToolsOpen = false; sourceImportOpen = false; sourceQuery = { q:'', site:'', topic:'', status:'' }; sourceQueryDraft = { ...sourceQuery }; sourceCatalogOffset = 0; sourceSelection = new Map(); sourceCatalogLoading = false;
    ++searchReadRequest; searchSnapshot = null; searchDetail = null; searchDraft = null; searchOriginal = null; sourceDateFilter = '';
    ++mediaWeeklyRequest; mediaWeekly = null; mediaWeeklyTopics = []; mediaWeeklyLoading = false; mediaWeeklyResult = null;
  }
  const localDate = (date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  function latestCompletedSunday() {
    const end = new Date(); end.setHours(12,0,0,0); end.setDate(end.getDate() - (end.getDay() || 7));
    return localDate(end);
  }
  function mediaWeekRange(end) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(end)) return null;
    const date = new Date(end+'T12:00:00Z');
    if (Number.isNaN(date.getTime()) || date.toISOString().slice(0,10) !== end || date.getUTCDay() !== 0) return null;
    date.setUTCDate(date.getUTCDate()-6);
    return { start:date.toISOString().slice(0,10), end };
  }
  function mediaWeeklyHtml() {
    if (!mediaWeekly) return '';
    const range = mediaWeekRange(mediaWeekly.week_end), result = mediaWeeklyResult;
    return `<section class="card settings-section"><form id="media-weekly-form" class="settings-form"><fieldset${sourceBusy || mediaWeeklyLoading ? ' disabled' : ''}><div class="card-head"><h2>${esc(tr('mediaWeekly'))}</h2><button type="button" class="btn" data-act="mediaWeeklyClose">${esc(tr('mediaWeeklyClose'))}</button></div><p>${esc(mediaWeekly.plan.name)}</p><p class="setting-hint">${esc(tr('webSearchPlanWindow', { start:mediaWeekly.plan.start_date, end:mediaWeekly.plan.end_date }))}</p><div class="settings-grid"><div class="setting-field"><label for="media-weekly-topic">${esc(tr('mediaWeeklyTopic'))}</label><select id="media-weekly-topic" data-weekly="topic_id">${mediaWeeklyTopics.map((topic) => `<option value="${esc(topic.topic_id)}"${topic.topic_id === mediaWeekly.topic_id ? ' selected' : ''}>${esc(topic.label)}</option>`).join('')}</select></div><div class="setting-field"><label for="media-weekly-end">${esc(tr('mediaWeeklyEnd'))}</label><input id="media-weekly-end" type="date" data-weekly="week_end" value="${esc(mediaWeekly.week_end)}" max="${latestCompletedSunday()}" required></div></div><p id="media-weekly-window" class="setting-hint">${esc(range ? tr('mediaWeeklyWindow',range) : tr('mediaWeeklyInvalid'))}</p><p class="setting-hint">${esc(tr('mediaWeeklyNote'))}</p><button type="submit" class="btn primary">${esc(tr(mediaWeeklyLoading ? 'loading' : 'mediaWeekly'))}</button></fieldset></form>${result ? `<div id="media-weekly-result" role="status"><p>${esc(tr('mediaWeeklyCreated'))} ${esc(tr('mediaWeeklyCounts',result))}</p><a class="btn primary" data-link href="/reports?topic=${enc(result.topic)}&amp;period=${enc(result.period)}">${esc(tr('mediaWeeklyOpen'))} →</a></div>` : ''}</section>`;
  }
  async function openMediaWeekly(id) {
    if (sourceBusy || mediaWeeklyLoading || !isAdmin()) return;
    const plan = searchSnapshot?.plans.find((item) => item.config.id === id)?.config; if (!plan) return;
    const request = ++mediaWeeklyRequest, epoch = sessionEpoch;
    mediaWeekly = { plan:clone(plan), topic_id:plan.id, week_end:latestCompletedSunday() };
    mediaWeeklyTopics = [{ topic_id:plan.id, label:tr('mediaWeeklyOwnTopic',{ name:plan.topic }) }];
    mediaWeeklyResult = null; mediaWeeklyLoading = true; sourceError = ''; sourceNotice = ''; await render();
    try {
      const reports = await sourceApi('/api/reports');
      if (request !== mediaWeeklyRequest || epoch !== sessionEpoch || !mediaWeekly) return;
      const topics = new Map();
      for (const report of reports) if (report.topic_id && !topics.has(report.topic_id)) topics.set(report.topic_id,{ topic_id:report.topic_id, label:report.label || report.topic_id });
      const match = [...topics.values()].find((topic) => topic.label.trim() === plan.topic.trim());
      if (!topics.has(plan.id)) topics.set(plan.id,mediaWeeklyTopics[0]);
      mediaWeeklyTopics = [...topics.values()]; mediaWeekly.topic_id = match?.topic_id || plan.id;
    } catch (error) { if (request === mediaWeeklyRequest && epoch === sessionEpoch) sourceError = tr('sourceReadFailed')+' '+error.message; }
    finally { if (request === mediaWeeklyRequest && epoch === sessionEpoch) { mediaWeeklyLoading = false; await render(); } }
  }
  async function createMediaWeekly(event) {
    event.preventDefault(); if (!mediaWeekly || sourceBusy || sourceRunning() || mediaWeeklyLoading) return;
    const { topic_id, week_end, plan } = mediaWeekly;
    if (!mediaWeeklyTopics.some((topic) => topic.topic_id === topic_id) || !mediaWeekRange(week_end) || week_end >= localDate(new Date())) { sourceError = tr('mediaWeeklyInvalid'); updateSources(); return; }
    mediaWeeklyResult = null;
    await sourceMutation(`/api/sources/search/${enc(plan.id)}/weekly`, { topic_id, week_end }, (data) => {
      if (typeof data.topic !== 'string' || typeof data.period !== 'string') throw new Error(tr('actionFailed'));
      mediaWeeklyResult = data; memo.clear(); sourceNotice = tr('mediaWeeklyCreated');
    });
  }
  function searchDates(days = 7) {
    const end = new Date(), start = new Date(); start.setDate(end.getDate() - days + 1);
    return { start_date:localDate(start), end_date:localDate(end) };
  }
  function newSearchDraft() {
    searchDetail = null;
    const baseId = 'shanghai-esg-green-finance'; let id = baseId, suffix = 2;
    while (searchSnapshot?.plans.some((item) => item.config.id === id)) id = baseId+'-'+suffix++;
    searchDraft = { id, name:'上海 ESG 与绿色金融', topic:'上海 ESG 与绿色金融', provider:'searxng', queries:['上海 ESG','上海 绿色金融','上海 可持续信息披露','Shanghai sustainable finance'], ...searchDates(), language:'zh-CN', max_results:10 };
    searchOriginal = clone(searchDraft);
  }
  const searchConfigured = () => !!searchSnapshot?.providers?.[searchDraft?.provider]?.configured;
  const searchDateKey = (status) => ({ in_range:'webSearchInRange', out_of_range:'webSearchOutOfRange', unknown:'webSearchUnknown', conflict:'webSearchConflict' }[status] || 'webSearchUnknown');
  function searchPublisher(item) {
    if (item.publisher) return item.publisher;
    try { return new URL(item.url).hostname; } catch { return ''; }
  }
  function searchPlansHtml() {
    const error = sourceReadErrors.search ? `<p class="error" role="alert">${esc(sourceReadErrors.search)}</p>` : '';
    const plans = searchSnapshot?.plans || [];
    return error + (plans.length ? plans.map(({ config }) => `<article><button type="button" class="search-plan" data-act="searchSelect" data-id="${esc(config.id)}" aria-pressed="${searchDetail?.config.id === config.id}"${sourceBusy ? ' disabled' : ''}><strong>${esc(config.name)}</strong><span>${esc(config.topic)}</span><small>${esc(config.provider === 'baidu' ? tr('webSearchBaiduRemoved') : 'SearXNG')} · ${esc(config.start_date)} – ${esc(config.end_date)}</small><small>${esc(config.queries.join(' / '))}</small></button><div class="search-plan-actions"><button type="button" class="btn" data-act="searchMaterials" data-id="${esc(config.id)}">${esc(tr('sourceViewMaterials'))}</button><button type="button" class="btn" data-act="searchRecheck" data-id="${esc(config.id)}"${sourceBusy || sourceRunning() ? ' disabled' : ''}>${esc(tr('webSearchRecheck'))}</button><button type="button" class="btn" data-act="mediaWeeklyOpen" data-id="${esc(config.id)}"${sourceBusy || sourceRunning() || mediaWeeklyLoading ? ' disabled' : ''}>${esc(tr('mediaWeekly'))}</button></div></article>`).join('') : `<p class="empty">${esc(tr('webSearchEmpty'))}</p>`);
  }
  function searchReadinessHtml() {
    // A plan saved for Baidu (removed) still loads; the server refuses to save or run it.
    return `${searchDraft?.provider === 'baidu' ? `<p class="error">${esc(tr('webSearchBaiduRemovedHint'))}</p>` : `<p>${esc(tr(searchConfigured() ? 'webSearchReady' : 'webSearchUnavailable'))}</p>`}<a data-link href="/settings#sources-settings-h">${esc(tr('webSearchSettings'))} →</a>`;
  }
  function searchField(key, label, type = 'text') {
    return `<div class="setting-field"><label for="search-${key}">${esc(tr(label))}</label><input id="search-${key}" data-search="${key}" type="${type}" value="${esc(searchDraft[key])}"${key === 'id' && searchDetail ? ' disabled' : ''}${['name','topic'].includes(key) ? ' maxlength="120"' : ''}${['region','institution'].includes(key) ? ' maxlength="60"' : ''}${type === 'number' ? ' min="1" max="20" step="1"' : ''}${['region','institution'].includes(key) ? '' : ' required'}></div>`;
  }
  function searchWorkspaceHtml() {
    if (!searchDraft) newSearchDraft();
    return `<div class="search-workspace"><section class="card search-plans"><div class="card-head"><h2>${esc(tr('webSearchPlans'))}</h2><button type="button" class="btn" data-act="searchNew"${sourceBusy ? ' disabled' : ''}>+ ${esc(tr('webSearchNew'))}</button></div><div id="search-plans">${searchPlansHtml()}</div></section><form id="web-search-form" class="settings-form"><fieldset${sourceBusy ? ' disabled' : ''}><section class="card settings-section search-editor"><h2>${esc(tr('webSearch'))}</h2><p class="sub">${esc(tr('webSearchNote'))}</p><div class="settings-grid">${searchField('name','webSearchName')}${searchField('topic','topic')}<div class="setting-field"><label for="search-provider">${esc(tr('webSearchProvider'))}</label><select id="search-provider" data-search="provider"><option value="searxng"${searchDraft.provider === 'searxng' ? ' selected' : ''}>SearXNG</option>${searchDraft.provider === 'baidu' ? `<option value="baidu" selected disabled>${esc(tr('webSearchBaiduRemoved'))}</option>` : ''}</select></div><div class="setting-field"><label for="search-language">${esc(tr('webSearchLanguage'))}</label><select id="search-language" data-search="language">${[['zh-CN','中文'],['en','English']].map(([value,label]) => `<option value="${value}"${searchDraft.language === value ? ' selected' : ''}>${label}</option>`).join('')}</select></div></div><div id="search-readiness" class="search-readiness">${searchReadinessHtml()}</div><div class="setting-field"><label for="search-queries">${esc(tr('webSearchQueries'))}</label><textarea id="search-queries" data-search="queries" rows="5" aria-describedby="search-queries-hint" required>${esc(searchDraft.queries.join('\n'))}</textarea><p id="search-queries-hint" class="setting-hint">${esc(tr('webSearchQueriesHint'))}</p><p id="search-syntax-hint" class="setting-hint">${esc(tr('webSearchSyntax'))}</p></div><div class="settings-grid">${searchField('region','webSearchRegion')}${searchField('institution','webSearchInstitution')}</div><p class="setting-hint">${esc(tr('webSearchRegionHint'))}</p><div class="settings-grid">${searchField('start_date','webSearchStart','date')}${searchField('end_date','webSearchEnd','date')}</div><div class="settings-actions">${[7,30].map((days) => `<button type="button" class="btn" data-act="searchDates" data-days="${days}">${esc(tr(days === 7 ? 'webSearchRecent7' : 'webSearchRecent30'))}</button>`).join('')}</div><p id="search-window-note" class="setting-hint">${esc(tr('webSearchSearxngWindow'))}</p><details><summary>${esc(tr('webSearchAdvanced'))}</summary><div class="settings-grid">${searchField('id','webSearchId')}${searchField('max_results','webSearchMax','number')}</div></details><div class="settings-actions"><button type="submit" id="search-save" class="btn"${sourceRunning() || (searchDetail && !searchDirty()) ? ' disabled' : ''}>${esc(tr('webSearchSave'))}</button><button type="button" id="search-run" class="btn primary" data-act="searchRun"${sourceRunning() || !searchConfigured() ? ' disabled' : ''}>${esc(tr(searchDetail && !searchDirty() ? 'webSearchRunSaved' : 'webSearchRun'))}</button><button type="button" class="btn" data-act="searchDiscard"${!searchDirty() ? ' disabled' : ''}>${esc(tr('discardSettings'))}</button></div><p class="setting-hint">${esc(tr('webSearchManual'))}</p></section></fieldset></form></div>`;
  }
  async function readSearchPlans() {
    const request = ++searchReadRequest, epoch = sessionEpoch;
    try {
      const data = await sourceApi('/api/sources/search');
      if (request !== searchReadRequest || epoch !== sessionEpoch) return;
      searchSnapshot = data; delete sourceReadErrors.search;
      if (!searchDraft) {
        if (data.plans.length) { searchDetail = clone(data.plans[0]); searchDraft = clone(searchDetail.config); searchOriginal = clone(searchDetail.config); }
        else newSearchDraft();
      }
    } catch (error) { if (request === searchReadRequest && epoch === sessionEpoch) sourceReadErrors.search = tr('sourceReadFailed') + ' ' + error.message; }
  }
  function selectSearch(id) {
    if (sourceBusy || (searchDirty() && !window.confirm(tr('sourceLeave')))) return;
    const detail = searchSnapshot?.plans.find((item) => item.config.id === id); if (!detail) return;
    searchDetail = clone(detail); searchDraft = clone(detail.config); searchOriginal = clone(detail.config); sourceError = ''; sourceNotice = ''; render();
  }
  async function saveSearch(run = false, event) {
    event?.preventDefault(); if (!searchDraft || sourceBusy || sourceRunning() || (run && !searchConfigured())) return;
    const config = clone(searchDraft); config.name = config.name.trim(); config.topic = config.topic.trim(); for (const key of ['region','institution']) if (config[key] !== undefined) config[key] = config[key].trim(); config.queries = config.queries.map((query) => query.trim()).filter(Boolean);
    const validDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(config.id) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/.test(config.id) || !config.name || config.name.length > 120 || !config.topic || config.topic.length > 120 || !config.queries.length || config.queries.length > 8 || config.queries.some((query) => query.length > 200) || !validDate(config.start_date) || !validDate(config.end_date) || config.start_date > config.end_date || !Number.isInteger(config.max_results) || config.max_results < 1 || config.max_results > 20) { sourceError = tr('webSearchInvalid'); updateSources(); return; }
    let saved = !!searchDetail && !searchDirty();
    if (!saved) await sourceMutation('/api/sources/search', searchDetail ? { config, revision:searchDetail.revision } : { config }, (detail) => { searchDetail = clone(detail); searchDraft = clone(detail.config); searchOriginal = clone(detail.config); sourceNotice = tr('webSearchSaved'); saved = true; });
    if (!saved || !run || location.pathname !== '/sources') return;
    await sourceMutation('/api/sources/search/run', { config:clone(searchDetail.config), request_id:sourceRequestId() }, (data) => { sourceJobs = [data.job, ...sourceJobs.filter((job) => job.id !== data.job.id)]; sourceNotice = tr('sourceDiscoverySaved'); sourceView = 'jobs'; syncSourceUrl(); }, 'POST', true);
    scheduleSources();
  }
  async function recheckSearch(id, after) {
    if (sourceBusy || sourceRunning() || !id) return;
    await sourceMutation(`/api/sources/search/${enc(id)}/recheck`, { request_id:sourceRequestId(), ...(after ? { after } : {}) }, (data) => { sourceJobs = [data.job, ...sourceJobs.filter((job) => job.id !== data.job.id)]; sourceNotice = tr('sourceDiscoverySaved'); sourceView = 'jobs'; syncSourceUrl(); }, 'POST', true);
    scheduleSources();
  }
  function syncSourceUrl() {
    const url = new URL(location.href); url.searchParams.set('view',sourceView);
    for (const [key,value] of [['source',sourceFilter],['date_status',sourceDateFilter]]) { if (value) url.searchParams.set(key,value); else url.searchParams.delete(key); }
    history.replaceState(history.state, '', url);
  }
  function restoreSourceUrl() {
    const params = new URLSearchParams(location.search), view = params.get('view');
    sourceView = ['search','catalog','materials','jobs'].includes(view) ? view : 'catalog';
    sourceFilter = params.get('source') || ''; sourceFilterName = ''; sourceOffset = 0;
    sourceDateFilter = ['in_range','out_of_range','unknown','conflict'].includes(params.get('date_status')) ? params.get('date_status') : '';
    ++sourceMaterialRequest; sourceMaterials = null; delete sourceReadErrors.materials;
  }
  function sourceMaterialContextHtml() {
    const plan = sourceFilter.startsWith('search:') ? searchSnapshot?.plans.find((item) => item.config.id === sourceFilter.slice(7))?.config : null;
    const sourceName = sourceFilterName || sourcesSnapshot?.sources.find((item) => item.config.id === sourceFilter)?.config.name || sourceFilter;
    return `<div><p>${esc(plan ? tr('webSearchPlanContext', { name:plan.name }) : sourceFilter ? tr('sourceMaterialContext', { name:sourceName }) : tr('sourceAllMaterials'))}</p>${plan ? `<p class="setting-hint">${esc(tr('webSearchPlanWindow', { start:plan.start_date, end:plan.end_date }))}</p>` : ''}</div>${plan ? `<button type="button" class="btn" data-act="mediaWeeklyOpen" data-id="${esc(plan.id)}"${sourceBusy || sourceRunning() || mediaWeeklyLoading ? ' disabled' : ''}>${esc(tr('mediaWeekly'))}</button>` : ''}${sourceFilter ? `<button type="button" class="btn" data-act="sourceAllMaterials">${esc(tr('sourceAllMaterials'))}</button>` : ''}<button type="button" class="btn" data-act="sourceView" data-view="${plan ? 'search' : 'catalog'}">${esc(tr(plan ? 'webSearchPlans' : 'sourceCatalog'))}</button>`;
  }
  async function sourceApi(url, method = 'GET', body) {
    const response = await http(url, method === 'GET' ? { cache: 'no-store' } : { method, headers: { 'Content-Type': 'application/json', 'X-ISBE-CSRF': (url.startsWith('/api/sources/search') ? searchSnapshot?.csrf_token : sourcesSnapshot?.csrf_token) || session.csrf_token }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) { const error = new Error(response.status === 409 ? tr('sourceConflict') : data.error || tr('actionFailed')); error.status = response.status; throw error; }
    return data;
  }
  function sourceListHtml() {
    const error = sourceReadErrors.sources ? `<p class="error" role="alert">${esc(sourceReadErrors.sources)}</p>` : '';
    const list = sourcesSnapshot?.sources || [];
    const disabled = sourceBusy || sourceCatalogLoading || !!error;
    const rows = list.map((item) => `<tr><td><input type="checkbox" data-source-check="${esc(item.config.id)}" aria-label="${esc(tr('sourceSelectOne', { name:item.config.name }))}"${sourceSelection.has(item.config.id) ? ' checked' : ''}${disabled ? ' disabled' : ''}></td><td class="source-name-cell"><button type="button" class="source-name" data-act="sourceSelect" data-id="${esc(item.config.id)}"${sourceBusy ? ' disabled' : ''}>${esc(item.config.name)}</button><small class="mono">${esc(item.config.id)} · ${esc(tr(item.config.kind === 'rss' ? 'sourceRss' : 'sourcePage'))}</small><small class="source-mobile-site">${sourceLink(item.config.url,item.site)}</small></td><td class="source-site-cell">${sourceLink(item.config.url,item.site)}</td><td>${esc(item.config.topic)}</td><td><span class="chip ${item.config.enabled ? '' : 'plain'}">${esc(tr(item.config.enabled ? 'sourceActive' : 'sourceInactive'))}</span><small class="${item.last_status === 'failed' ? 'error' : ''}">${esc(tr({ ok:'sourceSuccess', failed:'sourceFailed', empty:'sourceNoEntries' }[item.last_status] || 'sourceNever'))}</small></td><td class="source-date-cell">${esc(sourceDate(item.last_checked))}</td><td class="source-number mono">${num(item.item_count)}</td><td><div class="source-row-actions"><button type="button" class="btn" data-act="sourceSelect" data-id="${esc(item.config.id)}"${sourceBusy ? ' disabled' : ''}>${esc(tr('sourceConfigure'))}</button><button type="button" class="btn" data-act="sourceMaterialsFor" data-id="${esc(item.config.id)}" data-name="${esc(item.config.name)}">${esc(tr('sourceViewMaterials'))}</button></div></td></tr>`).join('');
    return error + `<div class="source-catalog-caption"><p>${esc(tr('sourceMatches', { n:sourcesSnapshot?.total ?? 0 }))}</p>${sourceCatalogLoading ? `<span role="status">${esc(tr('loading'))}</span>` : ''}</div><div class="source-batch"><label><input type="checkbox" id="source-select-page" data-change="sourceSelectPage"${list.length && list.every((item) => sourceSelection.has(item.config.id)) ? ' checked' : ''}${disabled || !list.length ? ' disabled' : ''}>${esc(tr('sourceSelectPage'))}</label><span>${esc(tr('sourceSelected', { n:sourceSelection.size }))}</span><button type="button" class="btn" data-act="sourceBatch" data-enabled="true"${disabled || !sourceSelection.size || sourceRunning() ? ' disabled' : ''}>${esc(tr('sourceEnableBatch'))}</button><button type="button" class="btn" data-act="sourceBatch" data-enabled="false"${disabled || !sourceSelection.size || sourceRunning() ? ' disabled' : ''}>${esc(tr('sourceDisableBatch'))}</button><button type="button" class="btn" data-act="sourceClearSelection"${sourceBusy || !sourceSelection.size ? ' disabled' : ''}>${esc(tr('sourceClearSelection'))}</button></div>${list.length ? `<div class="source-table-scroll" data-page="${sourcesSnapshot?.offset || 0}" tabindex="0" role="region" aria-label="${esc(tr('sourceCatalog'))}"><table class="source-table"><thead><tr><th aria-label="${esc(tr('sourceSelectPage'))}"></th>${['sourceName','sourceWebsite','topic','sourceStatus','sourceChecked','sourceCount','sourceActions'].map((key) => `<th scope="col">${esc(tr(key))}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>` : error || sourceCatalogLoading ? '' : `<p class="empty">${esc(tr(sourcesSnapshot?.summary?.total ? 'sourceNoMatches' : 'sourceEmpty'))}</p>`}${sourcePager('sourceCatalogPage',sourcesSnapshot?.offset || 0,list.length,sourcesSnapshot?.total || 0,sourcesSnapshot?.limit || 50,disabled)}`;
  }
  function sourcePager(action,offset,count,total,limit,disabled = false) {
    return `<div class="pager"><button type="button" class="btn" data-act="${action}" data-offset="${Math.max(0,offset-limit)}"${disabled || !offset ? ' disabled' : ''}>${esc(tr('sourcePrev'))}</button><span class="setting-hint">${esc(tr('sourceRange',{from:count ? offset+1 : 0,to:offset+count,total}))}</span><button type="button" class="btn" data-act="${action}" data-offset="${offset+limit}"${disabled || offset+limit >= total ? ' disabled' : ''}>${esc(tr('sourceNext'))}</button></div>`;
  }
  function sourceOverviewHtml() {
    if (!sourcesSnapshot) return '';
    return `<p class="eyebrow">${esc(tr('sourceOverview'))}</p><div class="source-overview">${[['total','sourceTotal',''],['enabled','sourceActive','enabled'],['disabled','sourceInactive','disabled'],['failed','sourceFailed','failed'],['empty','sourceNoEntries','empty'],['never','sourceNever','never']].map(([key,label,status]) => `<button type="button" data-act="sourceStatusFilter" data-status="${status}"${sourceBusy ? ' disabled' : ''}><span>${esc(tr(label))}</span><strong class="mono">${num(sourcesSnapshot.summary?.[key] || 0)}</strong></button>`).join('')}</div>`;
  }
  function sourceFacetHtml(key) {
    const needle = sourceQueryDraft[key].trim().toLowerCase();
    return (sourcesSnapshot?.facets?.[key === 'site' ? 'sites' : 'topics'] || []).filter((facet) => facet.value.toLowerCase().includes(needle)).slice(0,20).map((facet) => `<option value="${esc(facet.value)}">${num(facet.count)}</option>`).join('');
  }
  function sourceFiltersHtml() {
    return `<form id="source-search-form" class="source-search"><div class="setting-field source-search-text"><label for="source-search">${esc(tr('sourceSearch'))}</label><input id="source-search" type="search" data-source-query="q" value="${esc(sourceQueryDraft.q)}"></div>${['site','topic'].map((key) => `<div class="setting-field"><label for="source-query-${key}">${esc(tr(key === 'site' ? 'sourceWebsite' : 'topic'))}</label><input id="source-query-${key}" data-source-query="${key}" list="source-facet-${key}" value="${esc(sourceQueryDraft[key])}" autocomplete="off"><datalist id="source-facet-${key}">${sourceFacetHtml(key)}</datalist></div>`).join('')}<div class="setting-field"><label for="source-query-status">${esc(tr('sourceStatus'))}</label><select id="source-query-status" data-source-query="status">${[['','all'],['enabled','sourceActive'],['disabled','sourceInactive'],['failed','sourceFailed'],['empty','sourceNoEntries'],['never','sourceNever']].map(([value,key]) => `<option value="${value}"${sourceQueryDraft.status === value ? ' selected' : ''}>${esc(tr(key))}</option>`).join('')}</select></div><div class="source-search-actions"><button type="submit" class="btn primary"${sourceBusy ? ' disabled' : ''}>${esc(tr('sourceApply'))}</button><button type="button" class="btn" data-act="sourceResetFilters"${sourceBusy ? ' disabled' : ''}>${esc(tr('sourceReset'))}</button></div><p class="setting-hint">${esc(tr('sourceFacetHint'))}</p></form>`;
  }
  function sourceField(key, label, type = 'text') {
    const value = sourceDraft[key] ?? '';
    const input = key === 'kind' ? `<select id="source-${key}" data-source="${key}">${['rss', 'page'].map((kind) => `<option value="${kind}"${value === kind ? ' selected' : ''}>${esc(tr(kind === 'rss' ? 'sourceRss' : 'sourcePage'))}</option>`).join('')}</select>` : `<input id="source-${key}" data-source="${key}" type="${type}" value="${esc(value)}"${key === 'id' && sourceDetail ? ' disabled' : ''}${['name','topic'].includes(key) ? ' maxlength="120"' : ''}${type === 'number' ? ' min="1" max="200" step="1"' : ''}${key === 'link_pattern' ? '' : ' required'}>`;
    return `<div class="setting-field"><label for="source-${key}">${esc(tr(label))}</label>${input}</div>`;
  }
  function sourceFormHtml() {
    if (!sourceDraft) return '';
    return `<form id="source-form" class="settings-form"><fieldset${sourceBusy ? ' disabled' : ''}><section class="card settings-section"><h2>${esc(sourceDetail ? sourceDetail.config.name : tr('sourceNew'))}</h2><div class="settings-grid">${sourceField('id','sourceId')}${sourceField('name','sourceName')}${sourceField('topic','topic')}${sourceField('kind','sourceKind')}${sourceField('url','sourceUrl','url')}${sourceField('max_items','sourceMax','number')}${sourceField('link_pattern','sourcePattern')}</div><p class="setting-hint">${esc(tr('sourcePatternHint'))}</p><div class="topic-check"><label><input type="checkbox" data-source="enabled"${sourceDraft.enabled ? ' checked' : ''}>${esc(tr('sourceEnabled'))}</label></div><div id="source-details-status">${sourceDetailsStatusHtml()}</div><div class="settings-actions"><button type="submit" class="btn primary" id="source-save"${!sourceConfigDirty() || sourceRunning() ? ' disabled' : ''}>${esc(tr('sourceSave'))}</button><button type="button" class="btn" data-act="sourceDiscard">${esc(tr('discardSettings'))}</button>${sourceDetail ? `<button type="button" class="btn" data-act="sourceReload">${esc(tr('topicReload'))}</button><button type="button" class="btn" id="source-collect" data-act="sourceCollect"${sourceConfigDirty() || sourceRunning() ? ' disabled' : ''}>${esc(tr('sourceTrial'))}</button>` : ''}</div><p id="source-running-note" class="setting-hint">${sourceRunning() ? esc(tr('sourcePending')) : ''}</p></section></fieldset></form>`;
  }
  function sourceDetailsStatusHtml() {
    if (!sourceDetail) return '';
    return `<p class="setting-hint">${esc(tr('sourceChecked'))}：${esc(sourceDate(sourceDetail.last_checked))} · ${esc(sourceDetail.last_status || tr('sourceNever'))}${sourceDetail.discovered_from ? `<br>${sourceLink(sourceDetail.discovered_from, sourceDetail.discovered_from)}` : ''}</p>${sourceDetail.last_error ? `<p class="error">${esc(sourceDetail.last_error)}</p>` : ''}`;
  }
  function sourceJobsHtml() {
    const error = sourceReadErrors.jobs ? `<p class="error" role="alert">${esc(sourceReadErrors.jobs)}</p>` : '';
    return error + (sourceJobs.length ? sourceJobs.map((job) => {
      const stats = job.stats || {};
      const queryErrors = (stats.queries || []).filter((query) => ['failed','partial'].includes(query.state) || query.error);
      return `<article class="source-job"><div class="card-head"><h3>${esc(tr(job.operation === 'recheck' ? 'webSearchRecheck' : job.operation === 'search' ? 'webSearch' : job.operation === 'discover' ? 'sourceDiscover' : 'sourceTrial'))}${stats.source_id || stats.plan_id ? ` · ${esc(stats.source_id || stats.plan_id)}` : ''}</h3><span class="chip ${['failed','interrupted'].includes(job.state) ? 'danger' : ''}">${esc(tr(job.state === 'partial' ? 'sourcePartial' : taskStateKey(job.state)))}</span></div><p class="setting-hint">${esc(tr('taskCreated'))}：${esc(sourceDate(job.created_at))}</p>${job.state !== 'running' && !job.error ? `<p>${esc(job.operation === 'recheck' ? tr('webSearchRecheckStats', { checked:stats.checked || 0, remaining:stats.remaining || 0 }) : tr('sourceStats', { new: stats.new || 0, updated: stats.updated || 0, unchanged: stats.unchanged || 0 }))}</p>` : ''}${job.error ? `<p class="error">${esc(job.error)}</p>` : ''}${stats.candidates?.length ? `<ul>${stats.candidates.map((candidate) => `<li>${sourceLink(candidate.url, candidate.name || candidate.url)} · ${esc(tr({ enabled:'userActive', candidate:'sourceCandidate', existing:'sourceExisting' }[candidate.state] || 'sourceCandidate'))} · ${esc(tr('sourceEntries', { n: candidate.entries || 0 }))}${candidate.error ? `<p class="error">${esc(candidate.error)}</p>` : ''}</li>`).join('')}</ul>` : ''}${job.operation === 'recheck' && (['failed','interrupted'].includes(job.state) || (['succeeded','partial'].includes(job.state) && stats.next_cursor && stats.remaining > 0)) ? `<button type="button" class="btn" data-act="searchRecheck" data-id="${esc(stats.plan_id)}" data-after="${esc(['failed','interrupted'].includes(job.state) ? stats.after || '' : stats.next_cursor)}"${sourceBusy || sourceRunning() ? ' disabled' : ''}>${esc(tr(['failed','interrupted'].includes(job.state) ? 'webSearchRetryBatch' : 'webSearchContinue', { n:stats.remaining }))}</button>` : ''}${queryErrors.length ? `<ul>${queryErrors.map((query) => `<li><strong>${esc(query.query)}</strong> · ${esc(query.error || (query.unresponsive_engines ? tr('webSearchEnginesUnavailable', { n:query.unresponsive_engines }) : tr(query.state === 'failed' ? 'failed' : 'sourcePartial')))}</li>`).join('')}</ul>` : ''}${stats.limited ? `<p class="setting-hint">${esc(tr('webSearchLimited'))}</p>` : stats.truncated ? `<p class="setting-hint">${esc(tr('sourceTruncated'))}</p>` : ''}${stats.unexecuted?.length ? `<p class="setting-hint">${esc(tr('webSearchUnexecuted', { items:stats.unexecuted.map((name) => tr('webSearchC_' + name)).join(tr('webSearchListSep')) }))}</p>` : ''}</article>`;
    }).join('') : error ? '' : `<p class="empty">${esc(tr('sourceNoJobs'))}</p>`);
  }
  function sourceMaterialsHtml() {
    if (sourceReadErrors.materials) return `<p class="error" role="alert">${esc(sourceReadErrors.materials)}</p>`;
    if (!sourceMaterials) return '';
    const { items, total, offset, limit, counts } = sourceMaterials;
    return (counts ? `<p class="setting-hint source-counts">${esc(tr('sourceCounts', counts))}</p>` : '') + (items.length ? items.map((item) => `<article class="source-material"><h3>${sourceLink(item.url, `${item.kind === 'web_search' ? searchPublisher(item) : item.source_name} · ${item.title}`)}</h3><p class="setting-hint">${item.published_at ? `${esc(tr(item.kind === 'web_search' ? 'webSearchOriginalDate' : 'sourcePublished'))}：${esc(item.kind === 'web_search' ? item.published_at.slice(0,10) : /^\d{4}-\d{2}-\d{2}$/.test(item.published_at) ? item.published_at : sourceDate(item.published_at))}` : esc(tr('sourceDateUnknown'))} · ${esc(tr('sourceFirstSeen'))}：${esc(sourceDate(item.first_seen))}${item.updated_at ? ` · ${esc(tr('sourceUpdated'))}：${esc(sourceDate(item.updated_at))}` : ''}${item.version_count > 1 ? ` · ${esc(tr('sourceVersions', { n: item.version_count }))}` : ''}</p>${item.kind === 'web_search' ? `<span class="chip plain search-date-status">${esc(tr(searchDateKey(item.date_status)))}</span>` : ''}${item.kind === 'web_search' && item.date_check_status === 'failed' && item.published_at ? `<p class="setting-hint">${esc(tr('webSearchRetainedDate'))}</p>` : ''}${item.summary ? `<p class="source-summary">${item.kind === 'web_search' ? `<span class="muted">${esc(tr('webSearchSummary'))} · </span>` : ''}${esc(item.summary)}</p>` : ''}${item.kind === 'web_search' ? `<details class="search-trace"><summary>${esc(tr('webSearchTrace'))}</summary><p>${esc(item.provider || '')}${item.search_queries?.length ? ` · ${esc(item.search_queries.join(' / '))}` : ''}</p>${item.start_date && item.end_date ? `<p>${esc(tr('webSearchWindow'))}：${esc(item.start_date)} – ${esc(item.end_date)}</p>` : ''}${item.read_status ? `<p>${esc(tr({ read:'webSearchDateRead', failed:'webSearchDateReadFailed', not_checked:'webSearchDateNotRead' }[item.read_status] || 'webSearchDateNotRead'))}</p>` : ''}${item.search_date_hint ? `<p>${esc(tr('webSearchDateHint'))}：${esc(item.search_date_hint)}</p>` : ''}</details>` : ''}${sourceTimelineHtml(item)}</article>`).join('') : `<p class="empty">${esc(tr(sourceDateFilter ? 'webSearchNoDateMatches' : 'sourceNoMaterials'))}</p>`) + `<div class="pager"><button type="button" class="btn" data-act="sourcePage" data-offset="${Math.max(0, offset-limit)}"${offset === 0 ? ' disabled' : ''}>${esc(tr('sourcePrev'))}</button><span class="setting-hint">${esc(tr('sourceRange', { from: items.length ? offset+1 : 0, to: offset+items.length, total }))}</span><button type="button" class="btn" data-act="sourcePage" data-offset="${offset+limit}"${offset+limit >= total ? ' disabled' : ''}>${esc(tr('sourceNext'))}</button></div>`;
  }
  function sourceTimelineHtml(item) {
    if (!(item.duplicates > 0)) return '';
    const open = sourceTimeline.id === item.id;
    const button = `<button type="button" class="btn" data-act="sourceTimeline" data-id="${esc(item.id)}" aria-expanded="${open}">${esc(tr(open ? 'sourceTimelineHide' : 'sourceDuplicates', { n:item.duplicates }))}</button>`;
    if (!open) return button;
    if (sourceTimeline.error) return `${button}<p class="error" role="alert">${esc(sourceTimeline.error)}</p>`;
    if (!sourceTimeline.data) return `${button}<p class="setting-hint">${esc(tr('sourceTimelineLoading'))}</p>`;
    const { events, counts } = sourceTimeline.data;
    const label = (event) => event.kind === 'search_hit' ? tr('sourceTimelineHit', { plan:event.plan_name || event.plan_id, query:event.query, rank:event.rank }) : event.kind === 'source_version' ? tr('sourceTimelineVersion', { source:event.source_name || event.source_id }) : tr('sourceTimelineFirstSeen', { plan:event.plan_id });
    return `${button}<div class="source-timeline"><h4>${esc(tr('sourceTimeline'))}</h4><p class="setting-hint">${esc(tr('sourceTimelineCounts', counts))} · ${esc(tr('sourceTimelineNote'))}</p><ol>${events.map((event) => `<li><span class="mono">${esc(sourceDate(event.at))}</span> · ${esc(label(event))} · ${sourceLink(event.url, event.title || event.url)}</li>`).join('')}</ol></div>`;
  }
  async function toggleSourceTimeline(id) {
    const request = ++sourceTimelineRequest;
    if (!id || sourceTimeline.id === id) { sourceTimeline = { id:null }; updateSources(); return; }
    sourceTimeline = { id }; updateSources();
    try {
      const data = await sourceApi(`/api/sources/materials/${enc(id)}/timeline`);
      if (request === sourceTimelineRequest) sourceTimeline = { id, data };
    } catch (error) { if (request === sourceTimelineRequest) sourceTimeline = { id, error:tr('sourceTimelineFailed') + ' ' + error.message }; }
    if (request === sourceTimelineRequest) updateSources();
  }
  function updateSources() {
    if (location.pathname !== '/sources' || !isAdmin()) return;
    const runningIndicator = $('#source-running-indicator'); if (runningIndicator) runningIndicator.hidden = !sourceRunning();
    const table = $('.source-table-scroll');
    const scroll = table && { page:table.dataset.page, top:table.scrollTop, left:table.scrollLeft };
    for (const [id, html] of [['source-overview',sourceOverviewHtml()], ['source-list',sourceListHtml()], ['source-jobs',sourceJobsHtml()], ['source-materials',sourceMaterialsHtml()], ['search-plans',searchPlansHtml()], ['search-readiness',searchReadinessHtml()], ['source-material-context',sourceMaterialContextHtml()]]) {
      const element = $('#'+id); if (element && element.innerHTML !== html) element.innerHTML = html;
    }
    const updatedTable = $('.source-table-scroll');
    if (updatedTable && scroll && updatedTable.dataset.page === scroll.page) { updatedTable.scrollTop = scroll.top; updatedTable.scrollLeft = scroll.left; }
    for (const key of ['site','topic']) { const facet = $('#source-facet-'+key); if (facet) facet.innerHTML = sourceFacetHtml(key); }
    const selectedPage = $('#source-select-page'); if (selectedPage) selectedPage.indeterminate = sourceSelection.size > 0 && sourceSelection.size < (sourcesSnapshot?.sources.length || 0);
    const status = $('#source-status'); if (status) { status.textContent = sourceError || sourceNotice || (sourceConfigDirty() || searchDirty() ? tr('sourceUnsaved') : ''); status.className = sourceError ? 'error' : 'topic-status'; }
    const searchSave = $('#search-save'); if (searchSave) searchSave.disabled = sourceBusy || sourceRunning() || (!!searchDetail && !searchDirty());
    const searchRun = $('#search-run'); if (searchRun) { searchRun.disabled = sourceBusy || sourceRunning() || !searchConfigured(); searchRun.textContent = tr(searchDetail && !searchDirty() ? 'webSearchRunSaved' : 'webSearchRun'); }
    const searchDiscard = $('[data-act="searchDiscard"]'); if (searchDiscard) searchDiscard.disabled = sourceBusy || !searchDirty();
    const save = $('#source-save'); if (save) save.disabled = sourceBusy || sourceRunning() || !sourceConfigDirty();
    const collect = $('#source-collect'); if (collect) collect.disabled = sourceBusy || sourceRunning() || sourceConfigDirty();
    const importButton = $('#source-import'); if (importButton) importButton.disabled = sourceBusy || sourceRunning();
    const runningNote = $('#source-running-note'); if (runningNote) runningNote.textContent = sourceRunning() ? tr('sourcePending') : '';
    const detailsStatus = $('#source-details-status'); if (detailsStatus) detailsStatus.innerHTML = sourceDetailsStatusHtml();
  }
  function scheduleSources() {
    clearTimeout(sourceTimer);
    if (location.pathname === '/sources' && isAdmin() && !sourceBusy && sourceRunning() && !sourceReadErrors.jobs) sourceTimer = setTimeout(() => refreshSources(true), 2000);
  }
  async function readSourceMaterials() {
    if (!isAdmin()) return;
    const materialRequest = ++sourceMaterialRequest;
    try {
      const data = await sourceApi(`/api/sources/materials?source=${enc(sourceFilter)}&offset=${sourceOffset}${sourceDateFilter ? `&date_status=${enc(sourceDateFilter)}` : ''}`);
      if (materialRequest !== sourceMaterialRequest) return;
      sourceMaterials = data; delete sourceReadErrors.materials;
    } catch (error) { if (materialRequest === sourceMaterialRequest) sourceReadErrors.materials = tr('sourceReadFailed') + ' ' + error.message; }
  }
  async function readSourceCatalog() {
    if (!isAdmin()) return;
    const catalogRequest = ++sourceCatalogRequest;
    sourceCatalogLoading = true; updateSources();
    try {
      const query = new URLSearchParams({ ...sourceQuery, offset:String(sourceCatalogOffset) });
      const data = await sourceApi('/api/sources?'+query);
      if (catalogRequest !== sourceCatalogRequest) return;
      if (data.offset > 0 && data.offset >= data.total) {
        sourceCatalogOffset = data.total ? Math.floor((data.total-1)/data.limit)*data.limit : 0;
        sourceSelection.clear(); await readSourceCatalog(); return;
      }
      sourcesSnapshot = data; sourceCatalogOffset = data.offset; delete sourceReadErrors.sources;
      const ids = new Set(data.sources.map((item) => item.config.id));
      for (const id of sourceSelection.keys()) if (!ids.has(id)) sourceSelection.delete(id);
      const latest = data.sources.find((item) => item.config.id === sourceDetail?.config.id);
      if (latest && !sourceConfigDirty() && latest.revision !== sourceDetail.revision) {
        sourceDetail = clone(latest); sourceDraft = clone(latest.config);
        const editor = $('#source-editor-form'); if (editor) editor.innerHTML = sourceFormHtml();
      } else if (latest) for (const key of ['last_checked','last_status','last_error','item_count']) sourceDetail[key] = latest[key];
    } catch (error) { if (catalogRequest === sourceCatalogRequest) sourceReadErrors.sources = tr('sourceReadFailed')+' '+error.message; }
    finally { if (catalogRequest === sourceCatalogRequest) { sourceCatalogLoading = false; updateSources(); } }
  }
  async function refreshSources(poll = false) {
    const request = ++sourceRequest;
    clearTimeout(sourceTimer);
    const read = async (section, url, accept) => {
      try { const data = await sourceApi(url); if (request !== sourceRequest) return; accept(data); delete sourceReadErrors[section]; }
      catch (error) { if (request === sourceRequest) sourceReadErrors[section] = tr(section === 'jobs' ? 'sourceJobReadFailed' : 'sourceReadFailed') + ' ' + error.message; }
    };
    await read('jobs', '/api/sources/jobs', (data) => { sourceJobs = data.jobs; });
    if (request !== sourceRequest) return;
    if (!poll || (!sourceRunning() && !sourceReadErrors.jobs)) await Promise.all([
      readSourceCatalog(), ...(sourceView === 'materials' ? [readSourceMaterials()] : []), ...(searchSnapshot || sourceView === 'search' || sourceFilter.startsWith('search:') ? [readSearchPlans()] : []),
    ]);
    if (request !== sourceRequest) return;
    sourceLoaded = true; updateSources(); scheduleSources();
  }
  async function sourcesPage() {
    if (!sourceLoaded) {
      restoreSourceUrl();
      await refreshSources();
    }
    const catalog = `<div id="source-overview">${sourceOverviewHtml()}</div><section class="card source-catalog">${sourceFiltersHtml()}<div id="source-list">${sourceListHtml()}</div></section>${sourceDraft ? `<div class="source-editor"><div class="source-editor-head"><button type="button" class="btn" data-act="sourceClose"${sourceBusy ? ' disabled' : ''}>${esc(tr('sourceCloseEditor'))} ×</button></div><div id="source-editor-form">${sourceFormHtml()}</div></div>` : ''}${sourceToolsHtml()}`;
    const inbox = `<section class="card source-inbox"><div class="card-head"><h2>${esc(tr('sourceInbox'))}</h2><button type="button" class="btn" data-act="sourceMaterialsRefresh">${esc(tr('sourceRefresh'))}</button></div><p class="sub">${esc(tr('sourceInboxNote'))}</p><div id="source-material-context" class="source-inbox-context">${sourceMaterialContextHtml()}</div><label class="pick source-date-filter">${esc(tr('webSearchDateFilter'))}<select data-change="sourceDateFilter">${[['','all'],['in_range','webSearchInRange'],['out_of_range','webSearchOutOfRange'],['unknown','webSearchUnknown'],['conflict','webSearchConflict']].map(([value,key]) => `<option value="${value}"${sourceDateFilter === value ? ' selected' : ''}>${esc(tr(key))}</option>`).join('')}</select></label><div id="source-materials">${sourceMaterialsHtml()}</div></section>`;
    const jobs = `<section class="card source-jobs-section"><h2>${esc(tr('sourceLatestJobs'))}</h2><div id="source-jobs" aria-live="polite">${sourceJobsHtml()}</div></section>`;
    return { main: `<header class="head"><div class="settings-intro"><h1 class="title">${esc(tr('sources'))}</h1><p class="sub">${esc(tr('sourcesNote'))}</p></div><button type="button" class="btn" data-act="sourcesRefresh"${sourceBusy ? ' disabled' : ''}>${esc(tr('sourceRefresh'))}</button></header><div class="source-workspace"><nav class="source-views" aria-label="${esc(tr('sources'))}">${[['search','webSearch'],['catalog','sourceCatalog'],['materials','sourceInbox'],['jobs','sourceHistory']].map(([view,key]) => `<button type="button" data-act="sourceView" data-view="${view}" aria-pressed="${sourceView === view}">${esc(tr(key))}${view === 'jobs' ? `<span id="source-running-indicator" class="source-running-dot" aria-label="${esc(tr('taskRunning'))}"${sourceRunning() ? '' : ' hidden'}></span>` : ''}</button>`).join('')}</nav><p id="source-status" role="status" aria-live="polite" class="${sourceError ? 'error' : 'topic-status'}">${esc(sourceError || sourceNotice || (sourceConfigDirty() || searchDirty() ? tr('sourceUnsaved') : ''))}</p><div id="media-weekly">${mediaWeeklyHtml()}</div>${sourceView === 'search' ? searchWorkspaceHtml() : sourceView === 'materials' ? inbox : sourceView === 'jobs' ? jobs : catalog}</div>` };
  }
  function sourceToolsHtml() {
    return `<details id="source-tools" class="card source-tools"${sourceToolsOpen ? ' open' : ''}><summary>${esc(tr('sourceTools'))}</summary><button type="button" class="btn" data-act="sourceNew"${sourceBusy ? ' disabled' : ''}>+ ${esc(tr('sourceNew'))}</button><form id="source-discovery-form" class="settings-form"><fieldset${sourceBusy ? ' disabled' : ''}><h2>${esc(tr('sourceDiscover'))}</h2><p class="sub">${esc(tr('sourceDiscoverNote'))}</p><div class="settings-grid"><div class="setting-field"><label for="source-site">${esc(tr('sourceSite'))}</label><input id="source-site" type="url" data-discovery="url" value="${esc(sourceDiscovery.url)}" required></div><div class="setting-field"><label for="source-discovery-topic">${esc(tr('topic'))}</label><input id="source-discovery-topic" data-discovery="topic" value="${esc(sourceDiscovery.topic)}" maxlength="120" required></div></div><div class="topic-check"><label><input type="checkbox" data-discovery="auto_enable"${sourceDiscovery.auto_enable ? ' checked' : ''}>${esc(tr('sourceAuto'))}</label></div><button class="btn primary" type="submit"${sourceRunning() ? ' disabled' : ''}>${esc(tr('sourceDiscoverStart'))}</button></fieldset></form><details id="source-import-details" class="source-import-section"${sourceImportOpen ? ' open' : ''}><summary>${esc(tr('sourceImportExport'))}</summary><p class="setting-hint">${esc(tr('sourceImportNote'))}</p><form id="source-import-form"><div class="setting-field"><label for="source-json">${esc(tr('sourceJson'))}</label><textarea id="source-json" rows="7" data-input="sourceImport"${sourceBusy ? ' disabled' : ''}>${esc(sourceImportText)}</textarea></div><div class="settings-actions"><button type="submit" id="source-import" class="btn"${sourceBusy || sourceRunning() ? ' disabled' : ''}>${esc(tr('sourceImport'))}</button><a class="btn" href="/api/sources/export" download="isbe-sources.json">${esc(tr('sourceExport'))}</a></div></form></details></details>`;
  }
  async function applySourceFilters(e) {
    e?.preventDefault(); if (sourceBusy) return;
    sourceQuery = Object.fromEntries(Object.entries(sourceQueryDraft).map(([key,value]) => [key,value.trim()]));
    sourceQueryDraft = { ...sourceQuery }; sourceCatalogOffset = 0; sourceSelection.clear();
    await readSourceCatalog();
  }
  async function showSourceView(view) {
    if (!isAdmin() || !['search','catalog','materials','jobs'].includes(view)) return;
    ++sourceDetailRequest; sourceView = view;
    syncSourceUrl();
    if (view === 'search' && !searchSnapshot) await readSearchPlans();
    const epoch = sessionEpoch; await render();
    if (epoch !== sessionEpoch || location.pathname !== '/sources' || sourceView !== view) return;
    if (view === 'materials') { await readSourceMaterials(); if (epoch === sessionEpoch) updateSources(); }
  }
  async function showSourceMaterials(id = '', name = '') {
    sourceFilter = id; sourceFilterName = name; sourceOffset = 0; sourceMaterials = null; delete sourceReadErrors.materials;
    await showSourceView('materials');
  }
  function selectCatalogSources(el) {
    if (sourceBusy || sourceCatalogLoading || sourceReadErrors.sources) return;
    const rows = sourcesSnapshot?.sources || [];
    if (el.dataset.change === 'sourceSelectPage') {
      sourceSelection.clear();
      if (el.checked) for (const item of rows) sourceSelection.set(item.config.id,item.revision);
    } else {
      const item = rows.find((item) => item.config.id === el.dataset.sourceCheck); if (!item) return;
      if (el.checked) sourceSelection.set(item.config.id,item.revision); else sourceSelection.delete(item.config.id);
    }
    updateSources();
    $(el.dataset.sourceCheck ? `[data-source-check="${el.dataset.sourceCheck}"]` : '#source-select-page')?.focus({ preventScroll:true });
  }
  async function batchSources(enabled) {
    if (sourceBusy || sourceRunning() || sourceCatalogLoading || sourceReadErrors.sources || !sourceSelection.size) return;
    const rows = sourcesSnapshot?.sources || [];
    const sources = rows.filter((item) => sourceSelection.has(item.config.id)).map((item) => ({ id:item.config.id, revision:sourceSelection.get(item.config.id) }));
    if (!sources.length) return;
    await sourceMutation('/api/sources/batch', { sources, enabled }, async (result) => {
      sourceSelection.clear(); sourceNotice = tr('sourceBatchSaved', { n:result.updated });
      if (sourceDetail && !sourceConfigDirty() && sources.some((item) => item.id === sourceDetail.config.id)) {
        const epoch = sessionEpoch;
        try { const detail = await sourceApi(`/api/sources/config/${enc(sourceDetail.config.id)}`); if (epoch === sessionEpoch) { sourceDetail = clone(detail); sourceDraft = clone(detail.config); } }
        catch (error) { if (epoch === sessionEpoch) sourceError = tr('sourceReadFailed')+' '+error.message; }
      }
    }, 'POST', false, 'sourceBatchConflict');
  }
  async function selectSource(id) {
    if (!isAdmin() || sourceBusy || (sourceConfigDirty() && !window.confirm(tr('sourceLeave')))) return;
    const request = ++sourceDetailRequest, epoch = sessionEpoch, draft = JSON.stringify(sourceDraft);
    try {
      const detail = await sourceApi(`/api/sources/config/${enc(id)}`);
      if (request !== sourceDetailRequest || epoch !== sessionEpoch || location.pathname !== '/sources' || draft !== JSON.stringify(sourceDraft)) return;
      sourceDetail = clone(detail); sourceDraft = clone(detail.config); sourceError = ''; sourceNotice = ''; sourceView = 'catalog'; await render(); $('#source-name')?.focus();
    } catch (error) { if (request === sourceDetailRequest && epoch === sessionEpoch) { sourceError = error.message; updateSources(); } }
  }
  function editSource(el) {
    if (sourceBusy) return;
    if (el.dataset.weekly && mediaWeekly && !mediaWeeklyLoading && ['topic_id','week_end'].includes(el.dataset.weekly)) {
      mediaWeekly[el.dataset.weekly] = el.value; mediaWeeklyResult = null; sourceError = ''; sourceNotice = '';
      const range = mediaWeekRange(mediaWeekly.week_end), hint = $('#media-weekly-window');
      if (hint) hint.textContent = range ? tr('mediaWeeklyWindow',range) : tr('mediaWeeklyInvalid');
      const result = $('#media-weekly-result'); if (result) result.innerHTML = '';
      updateSources();
    }
    if (el.dataset.search && searchDraft && !(el.dataset.search === 'id' && searchDetail)) {
      searchDraft[el.dataset.search] = el.dataset.search === 'max_results' ? Number(el.value) : el.dataset.search === 'queries' ? el.value.split('\n') : el.value;
      sourceError = ''; sourceNotice = ''; updateSources();
    }
    if (el.dataset.source && sourceDraft && !(el.dataset.source === 'id' && sourceDetail)) {
      sourceDraft[el.dataset.source] = el.type === 'checkbox' ? el.checked : el.dataset.source === 'max_items' ? Number(el.value) : el.value;
      sourceError = ''; sourceNotice = ''; updateSources();
    }
    if (el.dataset.discovery) { sourceDiscovery[el.dataset.discovery] = el.type === 'checkbox' ? el.checked : el.value; sourceDiscoveryDirty = true; }
    if (el.dataset.input === 'sourceImport') sourceImportText = el.value;
    if (el.dataset.sourceQuery) { sourceQueryDraft[el.dataset.sourceQuery] = el.value; const facet = $('#source-facet-'+el.dataset.sourceQuery); if (facet && ['site','topic'].includes(el.dataset.sourceQuery)) facet.innerHTML = sourceFacetHtml(el.dataset.sourceQuery); }
  }
  async function sourceMutation(url, body, accept, method = 'POST', job = false, conflict = 'sourceConflict') {
    if (sourceBusy || !isAdmin()) return;
    sourceBusy = true; sourceError = ''; sourceNotice = ''; clearTimeout(sourceTimer); ++sourceRequest; ++sourceCatalogRequest; ++sourceMaterialRequest; ++sourceDetailRequest; sourceCatalogLoading = false;
    const epoch = sessionEpoch; await render();
    if (epoch !== sessionEpoch || !isAdmin() || location.pathname !== '/sources') return;
    try {
      const data = await sourceApi(url, method, body);
      if (epoch !== sessionEpoch) return;
      await accept(data); if (epoch !== sessionEpoch) return; await refreshSources();
    } catch (error) { if (epoch === sessionEpoch) sourceError = job ? tr('sourceJobUnknown') + ' ' + error.message : error.status === 409 ? tr(conflict) : error.message; }
    finally { if (epoch === sessionEpoch) { sourceBusy = false; await render(); $('#source-status')?.scrollIntoView({ block:'nearest' }); } }
  }
  async function saveSource(e) {
    e.preventDefault(); if (!sourceDraft || sourceBusy || sourceRunning()) return;
    const config = clone(sourceDraft); config.name = config.name.trim(); config.topic = config.topic.trim(); config.url = config.url.trim();
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(config.id) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/.test(config.id) || !config.name || config.name.length > 120 || !config.topic || config.topic.length > 120 || !safeUrl(config.url) || !Number.isInteger(config.max_items) || config.max_items < 1 || config.max_items > 200) { sourceError = tr('sourceInvalid'); updateSources(); return; }
    await sourceMutation(sourceDetail ? `/api/sources/${enc(sourceDetail.config.id)}` : '/api/sources', sourceDetail ? { config, revision:sourceDetail.revision } : { config }, (detail) => { sourceDetail = clone(detail); sourceDraft = clone(detail.config); sourceNotice = tr('sourceSaved'); }, sourceDetail ? 'PUT' : 'POST');
  }
  function sourceRequestId() { return Array.from(crypto.getRandomValues(new Uint8Array(16)), (v) => v.toString(16).padStart(2,'0')).join(''); }
  async function discoverSources(e) {
    e.preventDefault(); if (sourceBusy) return;
    if (!safeUrl(sourceDiscovery.url) || !sourceDiscovery.topic.trim()) { sourceError = tr('sourceInvalid'); updateSources(); return; }
    await sourceMutation('/api/sources/discover', { ...sourceDiscovery, request_id:sourceRequestId() }, (data) => { sourceJobs = [data.job, ...sourceJobs.filter((job) => job.id !== data.job.id)]; sourceDiscoveryDirty = false; sourceNotice = tr('sourceDiscoverySaved'); }, 'POST', true);
  }
  async function collectSource() {
    if (!sourceDetail || sourceConfigDirty() || sourceBusy || sourceRunning()) return;
    await sourceMutation(`/api/sources/${enc(sourceDetail.config.id)}/collect`, { request_id:sourceRequestId() }, (data) => { sourceJobs = [data.job, ...sourceJobs.filter((job) => job.id !== data.job.id)]; sourceNotice = tr('sourceDiscoverySaved'); }, 'POST', true);
  }
  async function importSources(e) {
    e.preventDefault(); if (sourceBusy || sourceRunning()) return;
    let data;
    try { data = JSON.parse(sourceImportText); if (!data || !Array.isArray(data.sources)) throw new Error(); }
    catch { sourceError = tr('sourceBadJson'); updateSources(); return; }
    await sourceMutation('/api/sources/import', data, (result) => { sourceImportText = ''; sourceNotice = tr('sourceImported', { n:result.imported }); });
  }
  function leaveSources(nextPage) {
    if (currentPage !== '/sources' || nextPage === '/sources') return true;
    if (sourceBusy || (sourceDirty() && !window.confirm(tr('sourceLeave')))) return false;
    resetSources(); return true;
  }

  // ---------- routing ----------
  const PAGES = { '/': overviewPage, '/reports': reportPage, '/triage': triagePage, '/runs': runsPage, '/settings': settingsPage, '/topics': topicsPage, '/tasks': tasksPage, '/users': usersPage, '/sources': sourcesPage };
  let seq = 0;
  let historyIndex = Number(history.state?.isbeIndex) || 0;
  let restoringHistory = false;
  let currentPage = location.pathname;
  history.replaceState({ ...history.state, isbeIndex: historyIndex }, '', location.href);
  async function render() {
    const mine = ++seq;
    applyTheme();
    let page = PAGES[location.pathname] ? location.pathname : '/';
    clearTimeout(tasksTimer);
    clearTimeout(sourceTimer);
    try { await getSession(); } catch { authError = 'sessionUnavailable'; }
    if (mine !== seq) return;
    if (!session?.user) { $('#app').innerHTML = loginHtml(); document.title = 'ISBE · '+tr('login'); return; }
    if ((!isAdmin() && ['/settings','/runs','/triage','/users','/sources'].includes(page)) || (page === '/users' && session.mode !== 'shared')) {
      page = '/'; history.replaceState(history.state, '', '/'); currentPage = '/';
    }
    const q = new URLSearchParams(location.search);
    let html;
    try {
      const ov = !isAdmin() || ['/settings', '/topics', '/tasks', '/users', '/sources'].includes(page) ? null : await api(`/api/overview${page === '/reports' && ownerQuery() ? '?' + ownerQuery().slice(1) : ''}`);
      html = shell(page, ov, await PAGES[page](ov, q));
    } catch (err) {
      html = shell(page, null, { main: `<div class="card error" role="alert">${esc(err.message)}${['/settings', '/topics'].includes(page) ? `<button type="button" class="btn" data-act="${page === '/settings' ? 'retrySettings' : 'retryTopics'}">${esc(tr('retry'))}</button>` : ''}</div>` });
    }
    if (mine !== seq) return; // a newer render started meanwhile
    $('#app').innerHTML = html;
    updateGuide();
    const titles = { '/': 'overview', '/reports': 'reports', '/triage': 'triage', '/runs': 'runs', '/settings': 'settings', '/topics': 'topicSettings', '/tasks': 'tasks', '/users': 'users', '/sources': 'sources' };
    document.title = `ISBE · ${tr(titles[page])}`;
    if (page === '/tasks') scheduleTasks();
    if (page === '/sources') scheduleSources();
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }
  function leaveSettings(nextPage) {
    if (currentPage !== '/settings' || nextPage === '/settings') return true;
    if (settingsSaving || (settingsDirty() && !window.confirm(tr('leaveSettings')))) return false;
    settingsSnapshot = null;
    settingsEdits = {};
    settingsNotice = '';
    settingsError = '';
    return true;
  }
  function leaveTopics(nextPage) {
    if (currentPage !== '/topics' || nextPage === '/topics') return true;
    if (topicBusy || (topicDirty() && !window.confirm(tr('topicLeave')))) return false;
    topicsSnapshot = null; topicDetail = null; topicOriginal = null; topicEdits = {};
    topicError = ''; topicNotice = ''; topicConflict = false; ++topicRequest;
    return true;
  }
  const go = (url) => {
    if (mailBusy) return;
    const nextPage = new URL(url, location.href).pathname;
    if (!leaveSettings(nextPage) || !leaveTopics(nextPage) || !leaveSources(nextPage)) return;
    history.pushState({ isbeIndex: ++historyIndex }, '', url);
    if (guide.open) closeGuide();
    currentPage = nextPage;
    memo.clear();
    $('#app').querySelector('.main')?.insertAdjacentHTML('afterbegin', `<div class="muted" role="status">${esc(tr('loading'))}</div>`);
    render().then(() => window.scrollTo(0, 0));
  };
  // A change local to the page: same data, so the cache stays.
  const setParam = (k, v) => {
    const u = new URL(location.href);
    u.searchParams.set(k, v);
    history.replaceState(history.state, '', u);
    render();
  };

  const ACTIONS = {
    mediaWeeklyOpen: (b) => openMediaWeekly(b.dataset.id),
    mediaWeeklyClose: () => { if (!sourceBusy) { ++mediaWeeklyRequest; mediaWeekly = null; mediaWeeklyLoading = false; mediaWeeklyResult = null; render(); } },
    searchMaterials: (b) => { const plan = searchSnapshot?.plans.find((item) => item.config.id === b.dataset.id)?.config; if (plan) { sourceDateFilter = ''; return showSourceMaterials('search:'+plan.id,plan.name); } },
    searchRecheck: (b) => recheckSearch(b.dataset.id,b.dataset.after),
    searchSelect: (b) => selectSearch(b.dataset.id),
    searchNew: () => { if (sourceBusy || (searchDirty() && !window.confirm(tr('sourceLeave')))) return; newSearchDraft(); sourceError = ''; sourceNotice = ''; render(); },
    searchDiscard: () => { if (!sourceBusy && searchOriginal) { searchDraft = clone(searchOriginal); sourceError = ''; sourceNotice = ''; render(); } },
    searchDates: (b) => { const days = Number(b.dataset.days); if (!sourceBusy && searchDraft && [7,30].includes(days)) { Object.assign(searchDraft,searchDates(days)); sourceError = ''; sourceNotice = ''; render(); } },
    searchRun: () => saveSearch(true),
    sourcesRefresh: () => { if (!sourceBusy) return refreshSources(); },
    sourceSelect: (b) => selectSource(b.dataset.id),
    sourceReload: () => sourceDetail && selectSource(sourceDetail.config.id),
    sourceNew: () => {
      if (sourceBusy || (sourceConfigDirty() && !window.confirm(tr('sourceLeave')))) return;
      ++sourceDetailRequest; sourceView = 'catalog'; sourceDetail = null; sourceDraft = { id:'', name:'', topic:'', kind:'rss', url:'', enabled:true, link_pattern:'', max_items:100 }; sourceError = ''; sourceNotice = ''; render().then(() => $('#source-id')?.focus());
    },
    sourceDiscard: () => { if (!sourceBusy) { ++sourceDetailRequest; sourceDraft = sourceDetail ? clone(sourceDetail.config) : null; sourceError = ''; sourceNotice = ''; render(); } },
    sourceClose: () => { if (sourceBusy || (sourceConfigDirty() && !window.confirm(tr('sourceLeave')))) return; ++sourceDetailRequest; sourceDetail = null; sourceDraft = null; sourceError = ''; sourceNotice = ''; render(); },
    sourceView: (b) => showSourceView(b.dataset.view),
    sourceMaterialsFor: (b) => showSourceMaterials(b.dataset.id,b.dataset.name),
    sourceAllMaterials: () => showSourceMaterials(),
    sourceCatalogPage: async (b) => { const offset = Number(b.dataset.offset); if (sourceBusy || !Number.isSafeInteger(offset) || offset < 0) return; sourceCatalogOffset = offset; sourceSelection.clear(); await readSourceCatalog(); },
    sourceStatusFilter: async (b) => { if (sourceBusy) return; sourceQueryDraft = { q:'', site:'', topic:'', status:b.dataset.status }; sourceView = 'catalog'; await applySourceFilters(); await render(); },
    sourceResetFilters: async () => { if (sourceBusy) return; sourceQueryDraft = { q:'', site:'', topic:'', status:'' }; await applySourceFilters(); await render(); },
    sourceClearSelection: () => { if (!sourceBusy) { sourceSelection.clear(); updateSources(); } },
    sourceBatch: (b) => batchSources(b.dataset.enabled === 'true'),
    sourceCollect: collectSource,
    sourceMaterialsRefresh: async () => { await readSourceMaterials(); updateSources(); },
    sourcePage: async (b) => { sourceOffset = Number(b.dataset.offset); await readSourceMaterials(); updateSources(); },
    sourceTimeline: (b) => toggleSourceTimeline(b.dataset.id),
    guideOpen: (b) => openGuide(b.dataset.topic, b.dataset.fresh === 'true'),
    logout: signOut,
    tasksRefresh: refreshTasks,
    taskCancel: cancelTask,
    taskOpen: openTask,
    userActive: (b) => { if (b.dataset.active === 'true' || window.confirm(tr('userDisableConfirm'))) return changeUser('/api/users/'+enc(b.dataset.id), 'PUT', { active: b.dataset.active === 'true' }); },
    guideClose: closeGuide,
    guideCheck: async () => { if (!guide.loading && !guide.starting) { await checkGeneration(); await loadGuide(); } },
    guideNext: () => { if (!guide.loading && !guide.error && !guideDraft() && (guide.step === 1 ? guide.model : guideSourceReady())) { guide.step = Math.min(3, guide.step + 1); updateGuide(true); } },
    guideBack: () => { if (!guide.starting && !activeTask(guide.job)) { guide.step = Math.max(1, guide.step - 1); updateGuide(true); } },
    guideStart: startGeneration,
    guideAgain: () => { if (!guide.starting) { guide.job = null; guide.observed = null; guide.topic = ''; guide.step = 1; guide.interrupted = false; clearTimeout(guide.timer); loadGuide(); updateGuide(true); } },
    mailOpen: (b) => openMail(b),
    mailTest: (b) => openMail(b, true),
    mailClose: () => { if (!mailBusy) { mailDraft = null; mailResult = null; mailError = ''; render(); } },
    retryTopics: () => { memo.delete('/api/topics'); topicsSnapshot = null; render(); },
    topicSelect: (b) => selectTopic(b.dataset.id),
    topicReload: () => topicDetail?.id != null && selectTopic(topicDetail.id, true),
    topicNew: newTopic,
    topicDelete: deleteTopic,
    topicDiscard: () => {
      if (topicBusy) return;
      if (topicDetail?.id === null) { topicDetail = null; topicOriginal = null; }
      topicEdits = {}; topicError = ''; topicNotice = ''; render();
    },
    topicAddFeed: () => {
      if (!topicOriginal || topicBusy) return;
      const feeds = clone(topicFeeds()); feeds.push({ name: '', url: '' }); topicEdits['rss.feeds'] = feeds;
      updateTopicStatus(); render().then(() => $(`#topic-feed-${feeds.length - 1}-name`)?.focus());
    },
    topicRemoveFeed: (b) => {
      if (!topicOriginal || topicBusy) return;
      const feeds = clone(topicFeeds()); const index = Number(b.dataset.index);
      if (!Number.isInteger(index) || index < 0 || index >= feeds.length) return;
      feeds.splice(index, 1); topicEdits['rss.feeds'] = feeds; updateTopicStatus(); render();
    },
    retrySettings: () => { memo.delete('/api/settings'); render(); },
    discardSettings: () => { if (!settingsSaving) { settingsEdits = {}; settingsNotice = ''; settingsError = ''; render(); } },
    removeSecret: (b) => {
      if (settingsSaving) return;
      const key = b.dataset.key;
      if (settingsEdits[key] === null) delete settingsEdits[key];
      else settingsEdits[key] = null;
      settingsNotice = ''; settingsError = ''; render();
    },
    panel: () => { panel = !panel; render(); },
    lang: (b) => { prefs.lang = b.dataset.v; savePrefs(); render(); },
    mode: (b) => { prefs.mode = b.dataset.v; savePrefs(); render(); },
    theme: (b) => { prefs.theme = b.dataset.v; savePrefs(); render(); },
    paper: (b) => { prefs.paper = b.dataset.v; savePrefs(); render(); },
    reset: () => { prefs = { ...DEFAULTS, lang: prefs.lang }; savePrefs(); render(); },
    param: (b) => setParam(b.dataset.k, b.dataset.v),
    // The browser's print dialog saves the PDF; the title becomes its file name.
    print: (b) => {
      const title = document.title;
      document.title = b.dataset.name;
      window.addEventListener('afterprint', () => { document.title = title; }, { once: true });
      window.print();
    },
  };
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-link]');
    if (a && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
      e.preventDefault();
      go(a.getAttribute('href'));
      return;
    }
    const b = e.target.closest('[data-act]');
    if (b && ACTIONS[b.dataset.act]) ACTIONS[b.dataset.act](b);
  });
  document.addEventListener('change', (e) => {
    const el = e.target;
    editSource(el);
    if (el.dataset.sourceCheck || el.dataset.change === 'sourceSelectPage') selectCatalogSources(el);
    if (el.dataset.change === 'sourceDateFilter' && ['', 'in_range', 'out_of_range', 'unknown', 'conflict'].includes(el.value)) { sourceDateFilter = el.value; sourceOffset = 0; syncSourceUrl(); readSourceMaterials().then(updateSources); }
    if (el.dataset.change === 'guideTopic' && !guide.starting && !activeTask(guide.job)) { guide.topic = el.value; guide.job = null; guide.observed = null; guide.interrupted = false; loadGuide(); }
    if (el.dataset.topic || el.dataset.topicFeed !== undefined) editTopic(el);
    if (el.dataset.topic === 'arxivEnabled') render().then(() => $('#topic-arxivEnabled')?.focus());
    if (el.dataset.setting) editSetting(el);
    if (['ISBE_LLM_PROVIDER', 'ISBE_FULLTEXT_EXTRACTOR', 'EMAIL_TRANSPORT'].includes(el.dataset.setting)) render().then(() => $(`#setting-${el.dataset.setting}`)?.focus());
    if (el.dataset.change === 'triageTopic') go(`/triage?topic=${enc(el.value)}`);
    if (el.dataset.input === 'hue') { savePrefs(); render(); }
  });
  // The hue slider recolors live without re-rendering, so it keeps focus while dragged.
  document.addEventListener('input', (e) => {
    editSource(e.target);
    if (e.target.id === 'mail-recipients' && mailDraft && !mailBusy) { mailDraft.recipients = e.target.value; mailError = ''; }
    if (e.target.dataset.topic || e.target.dataset.topicFeed !== undefined) editTopic(e.target);
    if (e.target.dataset.input === 'topicSearch') { topicSearch = e.target.value; const list = $('#topic-list'); if (list && topicsSnapshot) list.innerHTML = topicListHtml(); }
    if (e.target.dataset.setting) editSetting(e.target);
    if (e.target.dataset.input !== 'hue') return;
    prefs.theme = 'custom';
    prefs.hue = Number(e.target.value);
    const v = $('#hue-v');
    if (v) v.textContent = `${prefs.hue}°`;
    applyTheme();
  });
  // An LLM call's prompt and reply load when its row is opened.
  const cut = (s, n = 40000) => { s = String(s ?? ''); return s.length > n ? `${s.slice(0, n)}\n… (${num(s.length - n)} ${tr('moreChars')})` : s; };
  document.addEventListener('toggle', async (e) => {
    const d = e.target;
    if (d instanceof HTMLDetailsElement && d.classList.contains('media-original')) { await toggleReportArticle(d); return; }
    if (d.id === 'settings-advanced') { settingsAdvanced = d.open; return; }
    if (d.id === 'settings-sources-advanced') { settingsSourcesAdvanced = d.open; return; }
    if (d.id === 'topic-rules') { topicRulesOpen = d.open; return; }
    if (d.id === 'source-tools') { sourceToolsOpen = d.open; return; }
    if (d.id === 'source-import-details') { sourceImportOpen = d.open; return; }
    if (!(d instanceof HTMLDetailsElement) || !d.classList.contains('call') || !d.open || d.dataset.loaded) return;
    d.dataset.loaded = '1';
    const body = d.querySelector('.body');
    try {
      const c = await api(`/api/call?id=${enc(d.dataset.id)}`);
      const p = c.prompt || {};
      body.innerHTML = `<div class="mono muted" style="font-size:12px">${esc(c.model)} · ${c.ms != null ? `${(c.ms / 1000).toFixed(1)} s` : '—'} · ${num(c.tokens_in)} → ${num(c.tokens_out)} tokens</div>
        <h4>system</h4><pre>${esc(cut(p.system))}</pre><h4>user</h4><pre>${esc(cut(p.user))}</pre><h4>response</h4><pre>${esc(cut(c.response))}</pre>`;
    } catch (err) {
      body.innerHTML = `<span class="error">${esc(err.message)}</span>`;
    }
  }, true);
  document.addEventListener('submit', (e) => e.target.id === 'login-form' ? signIn(e) : e.target.id === 'media-weekly-form' ? createMediaWeekly(e) : e.target.id === 'web-search-form' ? saveSearch(false,e) : e.target.id === 'source-search-form' ? applySourceFilters(e) : e.target.id === 'source-form' ? saveSource(e) : e.target.id === 'source-discovery-form' ? discoverSources(e) : e.target.id === 'source-import-form' ? importSources(e) : e.target.id === 'user-create' ? createUser(e) : e.target.dataset?.userPassword ? resetUserPassword(e) : e.target.id === 'mail-form' ? sendMail(e) : e.target.id === 'topic-form' ? saveTopic(e) : e.target.id === 'settings-form' ? saveSettings(e) : undefined);
  document.addEventListener('cancel', (e) => { if (e.target.id === 'report-guide') { e.preventDefault(); closeGuide(); } }, true);
  window.addEventListener('beforeunload', (e) => { if (settingsDirty() || settingsSaving || topicDirty() || topicBusy || mailBusy || sourceDirty() || sourceBusy) { e.preventDefault(); e.returnValue = ''; } });
  window.addEventListener('popstate', async (e) => {
    if (restoringHistory) { restoringHistory = false; return; }
    const nextIndex = Number(e.state?.isbeIndex) || 0;
    if (mailBusy || !leaveSettings(location.pathname) || !leaveTopics(location.pathname) || !leaveSources(location.pathname)) { restoringHistory = true; history.go(historyIndex - nextIndex); return; }
    historyIndex = nextIndex;
    currentPage = location.pathname;
    if (guide.open) closeGuide();
    if (currentPage === '/sources' && sourceLoaded) { restoreSourceUrl(); await refreshSources(); }
    memo.clear(); render();
  });
  render().then(() => { if (session?.user) { guide.observed = readGenerationMarker(); if (guide.observed) openGuide(guide.observed.topic); } });
})();
