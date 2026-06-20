/* ═══════════════════════════════════════════════════════
   vyrii UI  —  app.js
   All logic: state, i18n, API calls, tab handlers, UI utils
═══════════════════════════════════════════════════════ */

// ── Auth ──────────────────────────────────────────────
// Patch window.fetch to inject Basic Auth header when credentials are stored.
// When the server returns 401 (auth mode is on), show the login overlay.
(function () {
  const _orig = window.fetch.bind(window);

  window.fetch = function (url, opts) {
    opts = Object.assign({}, opts || {});
    // Only inject stored creds if the caller didn't pass an explicit Authorization header
    const hdrs = opts.headers || {};
    if (!hdrs['Authorization'] && !hdrs['authorization']) {
      const creds = sessionStorage.getItem('vyrii_creds');
      if (creds) {
        opts.headers = Object.assign({}, hdrs, { 'Authorization': 'Basic ' + creds });
      }
    }
    return _orig(url, opts).then(function (res) {
      if (res.status === 401) { _showLoginOverlay(); }
      return res;
    });
  };
})();

function _showLoginOverlay() {
  const el = document.getElementById('login-overlay');
  if (el) { el.style.display = 'flex'; applyLang(state ? state.lang : 'en'); }
}

function _hideLoginOverlay() {
  const el = document.getElementById('login-overlay');
  if (el) { el.style.display = 'none'; }
}

function doLogout() {
  sessionStorage.removeItem('vyrii_creds');
  // Hit the logout endpoint with fake credentials so the browser clears its Basic Auth cache.
  // Without this the browser auto-sends old cached credentials on the next probe and bypasses the overlay.
  fetch('/vyrii/auth/logout', {
    headers: { 'Authorization': 'Basic ' + btoa('logout:logout') }
  }).catch(() => {}).finally(() => _showLoginOverlay());
}

async function doLogin() {
  const user = (document.getElementById('login-user').value || '').trim();
  const pass = document.getElementById('login-pass').value || '';
  const errEl = document.getElementById('login-error');
  errEl.style.display = 'none';
  if (!user || !pass) { errEl.style.display = 'block'; return; }
  const creds = btoa(unescape(encodeURIComponent(user + ':' + pass)));
  try {
    // Test credentials explicitly — bypasses stored creds injection
    const res = await fetch('/v1/models', { headers: { 'Authorization': 'Basic ' + creds } });
    if (res.status === 401) { errEl.style.display = 'block'; return; }
    sessionStorage.setItem('vyrii_creds', creds);
    _hideLoginOverlay();
    loadModels();
    loadSettings();
  } catch (e) {
    errEl.style.display = 'block';
  }
}

// ── i18n ──────────────────────────────────────────────
const I18N = {
  en: {
    logo: 'V Y R I I', tagline: 'local AI tools',
    tab_chat: 'Chat', tab_translate: 'Translate', tab_obfuscate: 'Obfuscate',
    tab_files: 'Files', tab_webcrawl: 'WebCrawl', tab_webanalys: 'WebAnalys',
    tab_deepagent: 'DeepAgent', tab_scan: 'Scan', tab_webindex: 'WebIndex',
    label_model: 'Model', label_theme: 'Theme', label_lang: 'Language',
    new_chat: 'New chat', compact_chat: 'Compact', clear: 'Clear', send: 'Send', stop: 'Stop',
    compacting: 'Compacting…', compacted_ok: 'Conversation compacted',
    history: 'History', hist_search_ph: 'Search conversations…',
    hist_empty: 'No conversations yet',
    generating: 'Generating…', chat_empty: 'Start a conversation…',
    message_ph: 'Message… (Shift+Enter to send)',
    from_lang: 'From', to_lang: 'To', mode: 'Mode',
    source_text: 'Source text', translation: 'Translation',
    translate_btn: 'Translate', source_ph: 'Text to translate…',
    question: 'Question', question_ph: 'What would you like to know?',
    top_n: 'Top N', ask: 'Ask',
    task: 'Task', task_ph: 'Summarize the main content…',
    wc_mode: 'Mode', wc_filter: 'Filter', wc_ask: 'Ask LLM summary',
    wc_format: 'Output format', wc_columns: 'Columns (YAML / XPath)',
    max_pages: 'Max pages', crawl: 'Crawl',
    query: 'Query', query_ph: 'Search topic…', results: 'Results', analyze: 'Analyze',
    sections: 'Sections', generate: 'Generate',
    da_use_web: 'Web search', da_web_n: 'Results/section',
    da_use_rag: 'RAG',
    da_task_ph: 'Describe the document you want to generate…',
    path: 'Path', query_optional: 'Query (optional)',
    sc_query_ph: 'Filter by topic…',
    chunk: 'Chunk', summary_size: 'Summary', target: 'Target', rounds: 'Rounds',
    extensions: 'Extensions', compact: 'Compact',
    project_name: 'Project name', output_path: 'Output path', depth: 'Depth', index: 'Index',
    obfuscate_title: 'Obfuscate', deobfuscate_title: 'Deobfuscate',
    text: 'Text', glossary: 'Glossary name', force_mode: 'Force',
    obfuscate_btn: 'Obfuscate', deobfuscate_btn: 'Decode',
    of_ph: 'Text to obfuscate…', dof_ph: 'Obfuscated text to decode…',
    optional: '(optional)',
    refresh: 'Refresh', new_folder: 'New folder', upload: 'Upload',
    create: 'Create', cancel: 'Cancel', delete_btn: 'Delete',
    view: 'View', scan_btn: 'Scan', index_btn: 'Index',
    mkdir_ph: 'folder/name', select_file: 'Select a file or folder',
    loading: 'Loading…',
    result_here: 'Result will appear here…',
    copy: 'Copy', copy_raw: 'Copy raw markdown', copy_fmt: 'Copy formatted', copied: 'Copied!', add_to_chat: 'Add to chat',
    ctx_received: 'Context received. What would you like to know?',
    ctx_added: 'Added to chat context',
    login_btn: 'Log in', login_error: 'Invalid credentials', logout_btn: 'Log out',
    error_prefix: 'Error: ', no_model: 'No model selected',
    api_error: 'API error',
    show_thinking: 'Thinking', incognito: 'Incognito',
    thinking_label: 'Thinking…',
    active_profile: 'Active profile', no_profile: '— none (local only) —',
    stats_title: 'Backend Stats', stats_host: 'Host', stats_active: 'Active',
    stats_idle: 'idle', stats_busy: 'busy',
    queue_waiting: 'Waiting in queue...', retry_msg: 'Ask again',
    settings_reserve: 'Reserve mode', reserve_till_response: 'Till end of response',
    reserve_by_timer: 'By timer', lock_btn_lock: 'Locked', lock_btn_release: 'Released',
    lock_busy: 'Host is locked', lock_no_remote: 'Select a remote model first',
    // RAG
    tab_rag: 'RAG', rag_project: 'Project', rag_select_project: '— select project —',
    rag_query_ph: 'What are you looking for?', rag_results: 'Results',
    rag_sources: 'Sources', rag_llm_answer: 'LLM answer',
    ask_llm: 'Ask LLM', search: 'Search',
    // Settings
    tab_settings: 'Settings', settings_control: 'System control',
    settings_auth: 'Authentication', settings_auth_user: 'Username', settings_auth_pass: 'New password',
    sys_confirm: 'Confirm dangerous action',
    sys_restart: 'Restart vyrii', sys_reboot: 'Reboot PC', sys_shutdown: 'Shutdown PC',
    settings_connection: 'Connection',
    settings_backend: 'Backend', settings_timeouts: 'Timeouts',
    settings_req_timeout: 'Request timeout (s)', settings_worker_timeout: 'Worker timeout (s)',
    settings_defaults: 'Defaults', settings_default_model: 'Default model',
    settings_lang_default: 'Language', save: 'Save', settings_saved: 'Saved!',
    // Team / Profile
    tab_profile: 'Profiles', tab_team: 'Team',
    profile_new: 'New', saved_profiles: 'Saved',
    profile_name: 'Name', profile_comment: 'Comment',
    workers: 'Workers', add_worker: '+ Worker',
    team_profile: 'Profile', team_combine: 'Combine', team_ctx_mode: 'Context',
    team_query_ph: 'Question for all workers…', aspects: 'Aspects (one per worker)',
    run: 'Run', da_use_team: 'Team',
    // Scheduler
    tab_scheduler: 'Scheduler', sch_tasks: 'Tasks', sch_add_task: 'Add new task',
    sch_name_label: 'Name', sch_command_label: 'Command', sch_stype_label: 'Schedule type',
    sch_time_label: 'Time HH:MM', sch_dow_label: 'Day of week', sch_interval_label: 'Interval',
    sch_create_btn: 'Create task', sch_toggle_btn: 'Enable/Disable', sch_run_now_btn: 'Run now',
    sch_delete_btn: 'Delete', sch_task_id_label: 'Task ID (first 8 chars)',
    sch_load_logs_btn: 'Load log list', scheduler_logs_section: 'View task logs',
    sch_name_placeholder: 'Morning crawl', sch_command_placeholder: 'simargl index files .',
    // Projects
    tab_projects: 'Projects', projects_desc: 'Project registry — name to local path. Used by simargl and svitovyd tabs.',
    proj_add: 'Add project', proj_name: 'Name', proj_path: 'Path',
    proj_desc_label: 'Description (optional)', proj_add_btn: 'Add', proj_select: 'Project',
    proj_delete_confirm: 'Delete project?',
    // simargl
    tab_simargl: 'simargl', simargl_desc: 'Task-to-code retrieval — index a project, then search by task description.',
    sim_index_desc: 'Indexes the selected project with simargl. Creates a semantic index in ~/.vyrii/.simargl/<project>/.',
    sim_store: 'Store dir', sim_index_btn: 'Index',
    sim_query: 'Task description', sim_query_ph: 'Fix memory leak in connection pool…',
    sim_top_k: 'Top K', sim_target: 'Target', sim_search_btn: 'Search',
    // svitovyd
    tab_svitovyd: 'svitovyd', svitovyd_desc: 'Project map — index code structure, then find/trace/deps/sym/keywords.',
    svy_index_desc: 'Scans project directory and writes .svitovyd/map.txt.',
    svy_depth: 'Depth', svy_index_btn: 'Index',
    svy_find_query: 'Query tokens', svy_run_btn: 'Run',
    svy_identifier: 'Identifier', svy_depth_label: 'Depth',
    svy_top_k: 'Top K', svy_kw_task: 'Task text (optional — extract mode)', svy_kw_fuzzy: 'Fuzzy',
    svy_idiff_prev: 'Previous map file path',
    // run output
    run_ok: 'Done (exit {code}, {dur}s)', run_error: 'Error (exit {code})',
    // simargl help texts
    sim_tab_help: 'You have a task. You do not know which files to edit. simargl reads git history and finds the most likely files.',
    sim_index_help: 'Reads all git commits in this project. Builds a semantic index. Do this once before searching.',
    sim_store_help: 'Folder where the index is saved. Use the default unless you have a reason to change it.',
    sim_search_help: 'Write what you want to do — like a Jira task title. simargl finds the files most likely to change.',
    sim_topn_help: 'Total results to return.',
    sim_topk_help: 'Candidates per search step. Higher = slower but more accurate.',
    sim_mode_label: 'Mode',
    sim_mode_help: 'file — search by file content. aggr — group results by module (good for vague queries). task — find files via commit history (best for specific bugs). refine — auto-expand your query with project terms from commits, then search files (use when you don\'t know project vocabulary).',
    sim_format_label: 'Format',
    sim_format_help: 'text — readable output. paths — file paths only. modules — module names only. json — raw JSON.',
    sim_sort_label: 'Sort', sim_sort_help: 'rank — by score. freq — by frequency (task only).',
    sim_diff_help: 'Include changed code snippets in task results.',
    sim_noblackholes_help: 'Exclude files that appear in almost every task (noise).',
    sim_stderr_help: 'Show stderr in result. Off by default. Enable to debug errors.',
    sim_rrf_btn: 'RRF Search',
    sim_rrf_help: 'Runs two or more searches and merges by rank position. A file in both task and file search ranks higher than a file in only one. Best: combine task:default,file:jina.',
    sim_rrf_sources_label: 'Sources',
    sim_rrf_sources_help: 'Comma-separated mode:project pairs. Each pair is an independent search. Files in multiple sources rank higher.',
    sim_rrf_topk_help: 'Candidates per source before merge.',
    sim_rrf_k_help: 'Damping constant (default 60). Higher = smaller gap between ranks.',
    sim_blend_help: '0.7 pushes down broad files (changelogs, relnotes). 1.0 = off.',
    sim_target_help: 'File — individual files. aggr — folders/packages. Task — similar historical tasks with their changed files.',
    // svitovyd help texts
    svy_tab_help: 'Scan your code and build a map of all functions, classes, and their links. Then explore the structure.',
    svy_index_help: 'Scan the project folder. Find all function and class definitions. Save links between them. Result: .svitovyd/map.txt.',
    svy_depth_help: '2 — scan definitions and calls only. 3 — also scan variables and parameters. Use 2 for most projects.',
    svy_find_help: 'Filter the map by keyword.\nExamples:\n  auth — find blocks with "auth"\n  auth !test — find "auth", skip "test"\n  \\insertUser — find exact identifier "insertUser"',
    svy_trace_help: 'Pick one function or class name. See who calls it. Useful when you want to know: what breaks if I change this?',
    svy_deps_help: 'Pick one function or class name. See what it calls. Useful when you want to know: what does this depend on?',
    svy_sym_help: 'Find functions that many others call, but which call very few. These are often good candidates for refactoring.',
    svy_kw_help: 'No task text — list the most used identifiers in this codebase. With task text — find identifiers related to that text.',
    svy_kw_fuzzy_help: 'Split camelCase and snake_case. Finds more matches. Example: "user" matches "getUserById".',
    svy_idiff_help: 'Compare two map snapshots. Shows what functions and files changed. Useful after a big refactor.',
    svy_idiff_prev_help: 'Path to the old map file. Copy .svitovyd/map.txt to another name before a refactor, then compare after.',
    // Prompts
    tab_prompts: 'Prompts', prompts_desc: 'Prompt library — save and search by name, model, or area.',
    prompts_filter_ph: 'Filter by name, model, area…',
    prm_add: 'Add prompt', prm_name: 'Name', prm_desc_label: 'Description',
    prm_model_label: 'Model', prm_area_label: 'Area', prm_prompt_label: 'Prompt text',
    prm_add_btn: 'Save', prm_none: 'No prompts yet',
  },
  uk: {
    logo: 'В И Р І Й', tagline: 'локальні ШІ інструменти',
    tab_chat: 'Чат', tab_translate: 'Переклад', tab_obfuscate: 'Обфускація',
    tab_files: 'Файли', tab_webcrawl: 'ВебКраулер', tab_webanalys: 'ВебАналіз',
    tab_deepagent: 'Глибокий Пошук', tab_scan: 'Скан', tab_webindex: 'ВебІндекс',
    label_model: 'Модель', label_theme: 'Тема', label_lang: 'Мова',
    new_chat: 'Новий чат', compact_chat: 'Компакт', clear: 'Очистити', send: 'Надіслати', stop: 'Стоп',
    compacting: 'Ущільнення…', compacted_ok: 'Розмову ущільнено',
    history: 'Історія', hist_search_ph: 'Пошук розмов…',
    hist_empty: 'Розмов ще немає',
    generating: 'Генерація…', chat_empty: 'Почніть розмову…',
    message_ph: 'Повідомлення… (Shift+Enter — надіслати)',
    from_lang: 'З', to_lang: 'На', mode: 'Режим',
    source_text: 'Вихідний текст', translation: 'Переклад',
    translate_btn: 'Перекласти', source_ph: 'Текст для перекладу…',
    question: 'Запитання', question_ph: 'Що ви хочете знати?',
    top_n: 'Топ N', ask: 'Запитати',
    task: 'Завдання', task_ph: 'Підсумуйте основний вміст…',
    wc_mode: 'Режим', wc_filter: 'Фільтр', wc_ask: 'Запитати LLM підсумок',
    wc_format: 'Формат виводу', wc_columns: 'Колонки (YAML / XPath)',
    max_pages: 'Макс. сторінок', crawl: 'Краулити',
    query: 'Запит', query_ph: 'Тема пошуку…', results: 'Результатів', analyze: 'Аналізувати',
    sections: 'Розділів', generate: 'Генерувати',
    da_use_web: 'Веб-пошук', da_web_n: 'Результатів/розділ',
    da_use_rag: 'RAG',
    da_task_ph: 'Опишіть документ, який хочете згенерувати…',
    path: 'Шлях', query_optional: 'Запит (необов\'язково)',
    sc_query_ph: 'Фільтр по темі…',
    chunk: 'Чанк', summary_size: 'Самарі', target: 'Ціль', rounds: 'Проходів',
    extensions: 'Розширення', compact: 'Сканувати',
    project_name: 'Назва проекту', output_path: 'Шлях виводу', depth: 'Глибина', index: 'Індексувати',
    obfuscate_title: 'Обфускувати', deobfuscate_title: 'Деобфускувати',
    text: 'Текст', glossary: 'Назва словника', force_mode: 'Форс',
    obfuscate_btn: 'Обфускувати', deobfuscate_btn: 'Декодувати',
    of_ph: 'Текст для обфускації…', dof_ph: 'Обфускований текст для декодування…',
    optional: '(необов\'язково)',
    refresh: 'Оновити', new_folder: 'Нова папка', upload: 'Завантажити',
    create: 'Створити', cancel: 'Скасувати', delete_btn: 'Видалити',
    view: 'Переглянути', scan_btn: 'Скан', index_btn: 'Індексувати',
    mkdir_ph: 'папка/назва', select_file: 'Оберіть файл або папку',
    loading: 'Завантаження…',
    result_here: 'Результат з\'явиться тут…',
    copy: 'Копіювати', copy_raw: 'Копіювати markdown', copy_fmt: 'Копіювати з форматуванням', copied: 'Скопійовано!', add_to_chat: 'Додати в чат',
    ctx_received: 'Контекст отримано. Що ви хочете дізнатися?',
    ctx_added: 'Додано в контекст чату',
    login_btn: 'Увійти', login_error: 'Невірні дані', logout_btn: 'Вийти',
    error_prefix: 'Помилка: ', no_model: 'Модель не обрана',
    api_error: 'Помилка API',
    show_thinking: 'Думки', incognito: 'Інкогніто',
    thinking_label: 'Міркування…',
    active_profile: 'Активний профіль', no_profile: '— немає (лише локальні) —',
    stats_title: 'Статистика бекендів', stats_host: 'Хост', stats_active: 'Активні',
    stats_idle: 'вільний', stats_busy: 'зайнятий',
    queue_waiting: 'Чекаю в черзі...', retry_msg: 'Запитати ще раз',
    settings_reserve: 'Режим резервування', reserve_till_response: 'До кінця відповіді',
    reserve_by_timer: 'За таймером', lock_btn_lock: 'Зайнято', lock_btn_release: 'Звільнено',
    lock_busy: 'Хост зайнято', lock_no_remote: 'Оберіть віддалену модель',
    // RAG
    tab_rag: 'RAG', rag_project: 'Проект', rag_select_project: '— оберіть проект —',
    rag_query_ph: 'Що шукаєте?', rag_results: 'Результати',
    rag_sources: 'Джерела', rag_llm_answer: 'Відповідь LLM',
    ask_llm: 'Запитати LLM', search: 'Пошук',
    // Settings
    tab_settings: 'Налаштування', settings_control: 'Керування системою',
    settings_auth: 'Автентифікація', settings_auth_user: 'Логін', settings_auth_pass: 'Новий пароль',
    sys_confirm: 'Підтвердити небезпечну дію',
    sys_restart: 'Перезапустити vyrii', sys_reboot: 'Перезавантажити ПК', sys_shutdown: 'Вимкнути ПК',
    settings_connection: 'З\'єднання',
    settings_backend: 'Бекенд', settings_timeouts: 'Таймаути',
    settings_req_timeout: 'Таймаут запиту (с)', settings_worker_timeout: 'Таймаут воркера (с)',
    settings_defaults: 'Типові значення', settings_default_model: 'Типова модель',
    settings_lang_default: 'Мова', save: 'Зберегти', settings_saved: 'Збережено!',
    // Team / Profile
    tab_profile: 'Профілі', tab_team: 'Колектив',
    profile_new: 'Новий', saved_profiles: 'Збережені',
    profile_name: 'Назва', profile_comment: 'Опис',
    workers: 'Воркери', add_worker: '+ Воркер',
    team_profile: 'Профіль', team_combine: 'Об\'єднання', team_ctx_mode: 'Контекст',
    team_query_ph: 'Питання для всіх воркерів…', aspects: 'Аспекти (один на воркер)',
    run: 'Запустити', da_use_team: 'Команда',
    tab_scheduler: 'Планувальник', sch_tasks: 'Задачі', sch_add_task: 'Додати задачу',
    sch_name_label: 'Назва', sch_command_label: 'Команда', sch_stype_label: 'Тип розкладу',
    sch_time_label: 'Час ГГ:ХХ', sch_dow_label: 'День тижня', sch_interval_label: 'Інтервал',
    sch_create_btn: 'Створити задачу', sch_toggle_btn: 'Увімк./Вимк.', sch_run_now_btn: 'Запустити зараз',
    sch_delete_btn: 'Видалити', sch_task_id_label: 'ID задачі (перші 8 символів)',
    sch_load_logs_btn: 'Список логів', scheduler_logs_section: 'Переглянути логи',
    sch_name_placeholder: 'Ранковий обхід', sch_command_placeholder: 'simargl index files .',
    tab_projects: 'Проекти', projects_desc: 'Реєстр проектів — назва до шляху. Використовується в simargl та svitovyd.',
    proj_add: 'Додати проект', proj_name: 'Назва', proj_path: 'Шлях',
    proj_desc_label: 'Опис (необов\'язково)', proj_add_btn: 'Додати', proj_select: 'Проект',
    proj_delete_confirm: 'Видалити проект?',
    tab_simargl: 'simargl', simargl_desc: 'Пошук коду за задачею — проіндексуйте проект і шукайте за описом задачі.',
    sim_index_desc: 'Індексує проект через simargl. Створює семантичний індекс у ~/.vyrii/.simargl/<project>/.',
    sim_store: 'Директорія зберігання', sim_index_btn: 'Індексувати',
    sim_query: 'Опис задачі', sim_query_ph: 'Виправити витік пам\'яті в connection pool…',
    sim_top_k: 'Top K', sim_target: 'Ціль', sim_search_btn: 'Знайти',
    tab_svitovyd: 'svitovyd', svitovyd_desc: 'Карта проекту — проіндексуйте структуру коду, потім знаходьте/відстежуйте.',
    svy_index_desc: 'Сканує директорію проекту і записує .svitovyd/map.txt.',
    svy_depth: 'Глибина', svy_index_btn: 'Індексувати',
    svy_find_query: 'Токени запиту', svy_run_btn: 'Запустити',
    svy_identifier: 'Ідентифікатор', svy_depth_label: 'Глибина',
    svy_top_k: 'Top K', svy_kw_task: 'Текст задачі (необов\'язково — режим витягу)', svy_kw_fuzzy: 'Нечіткий',
    svy_idiff_prev: 'Шлях до попереднього map-файлу',
    run_ok: 'Готово (код {code}, {dur}с)', run_error: 'Помилка (код {code})',
    sim_tab_help: 'Є задача. Не знаєш які файли міняти. simargl читає git-історію і знаходить найімовірніші файли.',
    sim_index_help: 'Читає всі git-коміти цього проекту. Будує семантичний індекс. Зроби це один раз перед пошуком.',
    sim_store_help: 'Папка де зберігається індекс. Використовуй значення за замовчуванням якщо немає причини міняти.',
    sim_search_help: 'Напиши що хочеш зробити — як назва Jira-задачі. simargl знайде найімовірніші файли для зміни.',
    sim_topn_help: 'Скільки результатів повернути.',
    sim_topk_help: 'Кандидатів на крок пошуку. Більше = точніше але повільніше.',
    sim_mode_label: 'Режим',
    sim_mode_help: 'file — пошук за вмістом файлів. aggr — групує по модулях (для розмитих запитів). task — знаходить файли через git-коміти (найкраще для конкретних багів). refine — автоматично розширює запит термінами з комітів, потім шукає файли (використовуй коли не знаєш словник проекту).',
    sim_format_label: 'Формат',
    sim_format_help: 'text — читабельний вивід. paths — тільки шляхи. modules — тільки модулі. json — сирий JSON.',
    sim_sort_label: 'Сортування', sim_sort_help: 'rank — за оцінкою. freq — за частотою (тільки task).',
    sim_diff_help: 'Включити фрагменти змін коду в результати задач.',
    sim_noblackholes_help: 'Виключити файли що присутні майже в кожній задачі (шум).',
    sim_stderr_help: 'Показати stderr у результаті. Вимкнено за замовчуванням. Увімкни для діагностики помилок.',
    sim_rrf_btn: 'RRF Пошук',
    sim_rrf_help: 'Запускає кілька пошуків і об\'єднує за позицією в рейтингу. Файл що є і в task і в file пошуку займає вище місце. Найкраще: task:default,file:jina.',
    sim_rrf_sources_label: 'Джерела',
    sim_rrf_sources_help: 'Пари mode:project через кому. Кожна пара — окремий пошук. Файли з кількох джерел займають вищі позиції.',
    sim_rrf_topk_help: 'Кандидатів з кожного джерела перед злиттям.',
    sim_rrf_k_help: 'Константа згасання (за замовчуванням 60). Більше = менший розрив між позиціями.',
    sim_blend_help: '0.7 опускає широкі файли (changelog, relnotes). 1.0 = вимкнено.',
    sim_target_help: 'file — окремі файли. aggr — папки/пакети. task — схожі задачі зі зміненими файлами.',
    svy_tab_help: 'Сканує код і будує карту всіх функцій, класів та зв\'язків між ними. Потім дозволяє досліджувати структуру.',
    svy_index_help: 'Сканує папку проекту. Знаходить всі визначення функцій і класів. Зберігає зв\'язки між ними. Результат: .svitovyd/map.txt.',
    svy_depth_help: '2 — сканує визначення і виклики. 3 — також сканує змінні і параметри. Для більшості проектів вистачає 2.',
    svy_find_help: 'Фільтрує карту за ключовим словом.\nПриклади:\n  auth — знайти блоки з "auth"\n  auth !test — знайти "auth", пропустити "test"\n  \\insertUser — знайти точний ідентифікатор "insertUser"',
    svy_trace_help: 'Введи ім\'я функції або класу. Побачиш хто її викликає. Корисно коли хочеш знати: що зламається якщо я це зміню?',
    svy_deps_help: 'Введи ім\'я функції або класу. Побачиш що вона викликає. Корисно коли хочеш знати: від чого це залежить?',
    svy_sym_help: 'Знаходить функції які багато хто викликає, але які самі мало що викликають. Часто гарні кандидати для рефакторингу.',
    svy_kw_help: 'Без тексту задачі — список найвживаніших ідентифікаторів у коді. З текстом задачі — знаходить ідентифікатори пов\'язані з цим текстом.',
    svy_kw_fuzzy_help: 'Розбиває camelCase і snake_case. Знаходить більше збігів. Приклад: "user" знайде "getUserById".',
    svy_idiff_help: 'Порівнює два знімки карти. Показує що змінилось у функціях і файлах. Корисно після великого рефакторингу.',
    svy_idiff_prev_help: 'Шлях до старого файлу карти. Скопіюй .svitovyd/map.txt під іншою назвою перед рефакторингом, потім порівняй після.',
    // Prompts
    tab_prompts: 'Промпти', prompts_desc: 'Бібліотека промптів — збережи і знайди за назвою, моделлю або темою.',
    prompts_filter_ph: 'Пошук за назвою, моделлю, темою…',
    prm_add: 'Додати промпт', prm_name: 'Назва', prm_desc_label: 'Опис',
    prm_model_label: 'Модель', prm_area_label: 'Тема', prm_prompt_label: 'Текст промпту',
    prm_add_btn: 'Зберегти', prm_none: 'Промптів ще немає',
  },
  de: {
    logo: 'V Y R I I', tagline: 'lokale KI-Werkzeuge',
    tab_chat: 'Chat', tab_translate: 'Übersetzen', tab_obfuscate: 'Verschleiern',
    tab_files: 'Dateien', tab_webcrawl: 'WebCrawl', tab_webanalys: 'WebAnalyse',
    tab_deepagent: 'DeepAgent', tab_scan: 'Scan', tab_webindex: 'WebIndex',
    label_model: 'Modell', label_theme: 'Design', label_lang: 'Sprache',
    new_chat: 'Neuer Chat', compact_chat: 'Kompakt', clear: 'Leeren', send: 'Senden', stop: 'Stop',
    compacting: 'Verdichtung…', compacted_ok: 'Gespräch verdichtet',
    history: 'Verlauf', hist_search_ph: 'Gespräche suchen…',
    hist_empty: 'Noch keine Gespräche',
    generating: 'Generiere…', chat_empty: 'Gespräch beginnen…',
    message_ph: 'Nachricht… (Shift+Enter zum Senden)',
    from_lang: 'Von', to_lang: 'Nach', mode: 'Modus',
    source_text: 'Quelltext', translation: 'Übersetzung',
    translate_btn: 'Übersetzen', source_ph: 'Zu übersetzender Text…',
    question: 'Frage', question_ph: 'Was möchten Sie wissen?',
    top_n: 'Top N', ask: 'Fragen',
    task: 'Aufgabe', task_ph: 'Hauptinhalt zusammenfassen…',
    wc_mode: 'Modus', wc_filter: 'Filter', wc_ask: 'LLM-Zusammenfassung',
    wc_format: 'Ausgabeformat', wc_columns: 'Spalten (YAML / XPath)',
    max_pages: 'Max. Seiten', crawl: 'Crawlen',
    query: 'Suchanfrage', query_ph: 'Suchthema…', results: 'Ergebnisse', analyze: 'Analysieren',
    sections: 'Abschnitte', generate: 'Generieren',
    da_use_web: 'Websuche', da_web_n: 'Ergebnisse/Abschnitt',
    da_use_rag: 'RAG',
    da_task_ph: 'Beschreiben Sie das Dokument, das Sie generieren möchten…',
    path: 'Pfad', query_optional: 'Suchanfrage (optional)',
    sc_query_ph: 'Nach Thema filtern…',
    chunk: 'Chunk', summary_size: 'Zusammenfassung', target: 'Ziel', rounds: 'Runden',
    extensions: 'Erweiterungen', compact: 'Scannen',
    project_name: 'Projektname', output_path: 'Ausgabepfad', depth: 'Tiefe', index: 'Indexieren',
    obfuscate_title: 'Verschleiern', deobfuscate_title: 'Entschleiern',
    text: 'Text', glossary: 'Glossarname', force_mode: 'Direkt',
    obfuscate_btn: 'Verschleiern', deobfuscate_btn: 'Entschleiern',
    of_ph: 'Zu verschleiernder Text…', dof_ph: 'Verschleierter Text zum Entschleiern…',
    optional: '(optional)',
    refresh: 'Aktualisieren', new_folder: 'Neuer Ordner', upload: 'Hochladen',
    create: 'Erstellen', cancel: 'Abbrechen', delete_btn: 'Löschen',
    view: 'Anzeigen', scan_btn: 'Scan', index_btn: 'Indexieren',
    mkdir_ph: 'ordner/name', select_file: 'Datei oder Ordner auswählen',
    loading: 'Laden…',
    result_here: 'Ergebnis erscheint hier…',
    copy: 'Kopieren', copy_raw: 'Markdown kopieren', copy_fmt: 'Formatiert kopieren', copied: 'Kopiert!', add_to_chat: 'Zum Chat hinzufügen',
    ctx_received: 'Kontext empfangen. Was möchten Sie wissen?',
    ctx_added: 'Zum Chat-Kontext hinzugefügt',
    login_btn: 'Anmelden', login_error: 'Ungültige Anmeldedaten', logout_btn: 'Abmelden',
    error_prefix: 'Fehler: ', no_model: 'Kein Modell ausgewählt',
    api_error: 'API-Fehler',
    show_thinking: 'Denken', incognito: 'Inkognito', thinking_label: 'Denkt nach…',
    active_profile: 'Aktives Profil', no_profile: '— keins (nur lokal) —',
    stats_title: 'Backend-Statistik', stats_host: 'Host', stats_active: 'Aktiv',
    stats_idle: 'frei', stats_busy: 'belegt',
    queue_waiting: 'Warte in der Warteschlange...', retry_msg: 'Erneut fragen',
    settings_reserve: 'Reservierungsmodus', reserve_till_response: 'Bis Antwortende',
    reserve_by_timer: 'Nach Timer', lock_btn_lock: 'Gesperrt', lock_btn_release: 'Freigegeben',
    lock_busy: 'Host ist gesperrt', lock_no_remote: 'Remote-Modell wählen',
    tab_rag: 'RAG', rag_project: 'Projekt', rag_select_project: '— Projekt wählen —',
    rag_query_ph: 'Wonach suchen Sie?', rag_results: 'Ergebnisse',
    rag_sources: 'Quellen', rag_llm_answer: 'LLM-Antwort',
    ask_llm: 'LLM fragen', search: 'Suchen',
    tab_settings: 'Einstellungen', settings_control: 'Systemsteuerung',
    settings_auth: 'Authentifizierung', settings_auth_user: 'Benutzername', settings_auth_pass: 'Neues Passwort',
    sys_confirm: 'Gefährliche Aktion bestätigen',
    sys_restart: 'vyrii neustarten', sys_reboot: 'PC neustarten', sys_shutdown: 'PC herunterfahren',
    settings_connection: 'Verbindung',
    settings_backend: 'Backend', settings_timeouts: 'Timeouts',
    settings_req_timeout: 'Anfrage-Timeout (s)', settings_worker_timeout: 'Worker-Timeout (s)',
    settings_defaults: 'Standardwerte', settings_default_model: 'Standardmodell',
    settings_lang_default: 'Sprache', save: 'Speichern', settings_saved: 'Gespeichert!',
    tab_profile: 'Profile', tab_team: 'Team',
    profile_new: 'Neu', saved_profiles: 'Gespeichert',
    profile_name: 'Name', profile_comment: 'Kommentar',
    workers: 'Worker', add_worker: '+ Worker',
    team_profile: 'Profil', team_combine: 'Kombinieren', team_ctx_mode: 'Kontext',
    team_query_ph: 'Frage an alle Worker…', aspects: 'Aspekte (einer pro Worker)',
    run: 'Ausführen', da_use_team: 'Team',
    tab_scheduler: 'Planer', tab_projects: 'Projekte', tab_simargl: 'simargl', tab_svitovyd: 'svitovyd',
    proj_select: 'Projekt', proj_add: 'Projekt hinzufügen', proj_name: 'Name', proj_path: 'Pfad',
    proj_add_btn: 'Hinzufügen', sim_index_btn: 'Indexieren', sim_search_btn: 'Suchen',
    svy_index_btn: 'Indexieren', svy_run_btn: 'Ausführen', sch_create_btn: 'Aufgabe erstellen',
    sch_toggle_btn: 'Aktivieren/Deaktivieren', sch_run_now_btn: 'Jetzt ausführen', sch_delete_btn: 'Löschen',
    run_ok: 'Fertig (Code {code}, {dur}s)', run_error: 'Fehler (Code {code})',
    sim_tab_help: 'Du hast eine Aufgabe. Du weißt nicht, welche Dateien du ändern musst. simargl liest die Git-Historie und findet die wahrscheinlichsten Dateien.',
    sim_index_help: 'Liest alle Git-Commits in diesem Projekt. Baut einen semantischen Index. Einmal machen, bevor du suchst.',
    sim_store_help: 'Ordner, in dem der Index gespeichert wird. Benutze den Standard, außer du hast einen Grund ihn zu ändern.',
    sim_search_help: 'Schreib, was du tun möchtest — wie ein Jira-Titel. simargl findet die Dateien, die sich am wahrscheinlichsten ändern.',
    sim_topk_help: 'Wie viele Ergebnisse anzeigen.',
    sim_mode_label: 'Modus', sim_mode_help: 'file — nach Dateiinhalt suchen. aggr — nach Modul gruppiert (für vage Anfragen). task — Dateien via Git-Commits (am besten für konkrete Bugs). refine — Query automatisch mit Projektbegriffen aus Commits erweitern (wenn Projektvokabular unbekannt).',
    sim_sort_label: 'Sortierung', sim_sort_help: 'rank — nach Score. freq — nach Häufigkeit (nur task).',
    sim_target_help: 'file — Dateien. aggr — Ordner/Pakete. task — ähnliche Aufgaben mit geänderten Dateien.',
    svy_tab_help: 'Scannt den Code und erstellt eine Karte aller Funktionen, Klassen und ihrer Verbindungen.',
    svy_index_help: 'Scannt den Projektordner. Findet alle Funktions- und Klassendefinitionen. Speichert Verbindungen. Ergebnis: .svitovyd/map.txt.',
    svy_depth_help: '2 — scannt Definitionen und Aufrufe. 3 — auch Variablen und Parameter. Für die meisten Projekte reicht 2.',
    svy_find_help: 'Filtert die Karte nach Schlüsselwort.\nBeispiele:\n  auth — findet Blöcke mit "auth"\n  auth !test — findet "auth", überspringt "test"\n  \\insertUser — findet genau "insertUser"',
    svy_trace_help: 'Gib einen Funktions- oder Klassenname ein. Sieh, wer ihn aufruft. Nützlich wenn du wissen willst: was bricht, wenn ich das ändere?',
    svy_deps_help: 'Gib einen Funktions- oder Klassenname ein. Sieh, was er aufruft. Nützlich wenn du wissen willst: wovon hängt das ab?',
    svy_sym_help: 'Findet Funktionen, die viele aufrufen, aber selbst wenig aufrufen. Oft gute Kandidaten für Refactoring.',
    svy_kw_help: 'Ohne Text — Liste der meistgenutzten Bezeichner im Code. Mit Text — findet Bezeichner zu diesem Text.',
    svy_kw_fuzzy_help: 'Trennt camelCase und snake_case. Findet mehr Treffer. Beispiel: "user" findet "getUserById".',
    svy_idiff_help: 'Vergleicht zwei Karten-Snapshots. Zeigt was sich geändert hat. Nützlich nach einem großen Refactoring.',
    svy_idiff_prev_help: 'Pfad zur alten Kartendatei. Kopiere .svitovyd/map.txt vor dem Refactoring, dann vergleiche danach.',
    // scheduler labels
    sch_tasks: 'Aufgaben', sch_add_task: 'Neue Aufgabe',
    sch_name_label: 'Name', sch_command_label: 'Befehl', sch_stype_label: 'Plantyp',
    sch_time_label: 'Zeit HH:MM', sch_dow_label: 'Wochentag', sch_interval_label: 'Intervall',
    sch_task_id_label: 'Aufgaben-ID (erste 8 Zeichen)',
    sch_load_logs_btn: 'Protokolle laden', scheduler_logs_section: 'Protokolle anzeigen',
    sch_name_placeholder: 'Morgendlicher Crawl', sch_command_placeholder: 'simargl index files .',
    // project labels
    projects_desc: 'Projektregister — Name zu lokalem Pfad. Verwendet von simargl und svitovyd.',
    proj_desc_label: 'Beschreibung (optional)', proj_delete_confirm: 'Projekt löschen?',
    // simargl labels
    simargl_desc: 'Code-Suche nach Aufgabe — Projekt indexieren, dann nach Aufgabenbeschreibung suchen.',
    sim_index_desc: 'Indexiert das Projekt mit simargl. Erstellt einen semantischen Index in ~/.vyrii/.simargl/<project>/.',
    sim_store: 'Speicherordner', sim_query: 'Aufgabenbeschreibung',
    sim_query_ph: 'Speicherleck im Connection Pool beheben…',
    sim_top_k: 'Top K', sim_target: 'Ziel',
    // svitovyd labels
    svitovyd_desc: 'Projektkarte — Code-Struktur indexieren, dann suchen/verfolgen/abhängigkeiten.',
    svy_index_desc: 'Scannt das Projektverzeichnis und schreibt .svitovyd/map.txt.',
    svy_depth: 'Tiefe', svy_find_query: 'Suchbegriffe',
    svy_identifier: 'Bezeichner', svy_depth_label: 'Tiefe',
    svy_top_k: 'Top K', svy_kw_task: 'Aufgabentext (optional — Extraktion)',
    svy_kw_fuzzy: 'Fuzzy', svy_idiff_prev: 'Pfad zur alten Kartendatei',
    // new search params
    sim_topn_help: 'Gesamtzahl der zurückzugebenden Ergebnisse.',
    sim_format_label: 'Format',
    sim_format_help: 'text — lesbar. paths — nur Dateipfade. modules — nur Modulnamen. json — roher JSON.',
    sim_diff_help: 'Geänderte Code-Ausschnitte in Ergebnissen einbeziehen.',
    sim_noblackholes_help: 'Dateien ausschließen, die in fast jeder Aufgabe erscheinen (Rauschen).',
    sim_stderr_help: 'Stderr anzeigen. Standardmäßig aus. Aktivieren um Fehler zu debuggen.',
    sim_rrf_btn: 'RRF-Suche', sim_rrf_sources_label: 'Quellen',
    sim_rrf_sources_help: 'Kommagetrennte mode:projekt Paare. Dateien in mehreren Quellen erhalten höhere Ränge.',
    sim_rrf_topk_help: 'Kandidaten pro Quelle vor dem Merge.',
    sim_rrf_k_help: 'Dämpfungskonstante (Standard 60).',
    sim_blend_help: '0.7 drückt breite Dateien (Changelog) nach unten. 1.0 = aus.',
    sim_rrf_help: 'Führt mehrere Suchen durch und fasst sie nach Rang zusammen. Dateien in task und file gleichzeitig erreichen höhere Positionen.',
    // Prompts
    tab_prompts: 'Prompts', prompts_desc: 'Prompt-Bibliothek — speichern und nach Name, Modell oder Bereich suchen.',
    prompts_filter_ph: 'Nach Name, Modell, Bereich filtern…',
    prm_add: 'Prompt hinzufügen', prm_name: 'Name', prm_desc_label: 'Beschreibung',
    prm_model_label: 'Modell', prm_area_label: 'Bereich', prm_prompt_label: 'Prompt-Text',
    prm_add_btn: 'Speichern', prm_none: 'Noch keine Prompts',
  },
  fr: {
    logo: 'V Y R I I', tagline: 'outils IA locaux',
    tab_chat: 'Chat', tab_translate: 'Traduction', tab_obfuscate: 'Obfuscation',
    tab_files: 'Fichiers', tab_webcrawl: 'WebCrawl', tab_webanalys: 'WebAnalyse',
    tab_deepagent: 'DeepAgent', tab_scan: 'Scan', tab_webindex: 'WebIndex',
    label_model: 'Modèle', label_theme: 'Thème', label_lang: 'Langue',
    new_chat: 'Nouveau chat', compact_chat: 'Compacter', clear: 'Effacer', send: 'Envoyer', stop: 'Stop',
    compacting: 'Compactage…', compacted_ok: 'Conversation compactée',
    history: 'Historique', hist_search_ph: 'Rechercher les conversations…',
    hist_empty: 'Aucune conversation',
    generating: 'Génération…', chat_empty: 'Commencez une conversation…',
    message_ph: 'Message… (Maj+Entrée pour envoyer)',
    from_lang: 'De', to_lang: 'Vers', mode: 'Mode',
    source_text: 'Texte source', translation: 'Traduction',
    translate_btn: 'Traduire', source_ph: 'Texte à traduire…',
    question: 'Question', question_ph: 'Que voulez-vous savoir ?',
    top_n: 'Top N', ask: 'Demander',
    task: 'Tâche', task_ph: 'Résumer le contenu principal…',
    wc_mode: 'Mode', wc_filter: 'Filtre', wc_ask: 'Résumé LLM',
    wc_format: 'Format de sortie', wc_columns: 'Colonnes (YAML / XPath)',
    max_pages: 'Pages max.', crawl: 'Crawler',
    query: 'Requête', query_ph: 'Sujet de recherche…', results: 'Résultats', analyze: 'Analyser',
    sections: 'Sections', generate: 'Générer',
    da_use_web: 'Recherche web', da_web_n: 'Résultats/section',
    da_use_rag: 'RAG',
    da_task_ph: 'Décrivez le document que vous souhaitez générer…',
    path: 'Chemin', query_optional: 'Requête (optionnelle)',
    sc_query_ph: 'Filtrer par sujet…',
    chunk: 'Chunk', summary_size: 'Résumé', target: 'Cible', rounds: 'Tours',
    extensions: 'Extensions', compact: 'Scanner',
    project_name: 'Nom du projet', output_path: 'Chemin de sortie', depth: 'Profondeur', index: 'Indexer',
    obfuscate_title: 'Obfusquer', deobfuscate_title: 'Désobfusquer',
    text: 'Texte', glossary: 'Nom du glossaire', force_mode: 'Forcé',
    obfuscate_btn: 'Obfusquer', deobfuscate_btn: 'Décoder',
    of_ph: 'Texte à obfusquer…', dof_ph: 'Texte obfusqué à décoder…',
    optional: '(optionnel)',
    refresh: 'Actualiser', new_folder: 'Nouveau dossier', upload: 'Téléverser',
    create: 'Créer', cancel: 'Annuler', delete_btn: 'Supprimer',
    view: 'Afficher', scan_btn: 'Scan', index_btn: 'Indexer',
    mkdir_ph: 'dossier/nom', select_file: 'Sélectionner un fichier ou dossier',
    loading: 'Chargement…',
    result_here: 'Le résultat apparaîtra ici…',
    copy: 'Copier', copy_raw: 'Copier le markdown', copy_fmt: 'Copier formaté', copied: 'Copié !', add_to_chat: 'Ajouter au chat',
    ctx_received: 'Contexte reçu. Que voulez-vous savoir ?',
    ctx_added: 'Ajouté au contexte du chat',
    login_btn: 'Connexion', login_error: 'Identifiants invalides', logout_btn: 'Déconnexion',
    error_prefix: 'Erreur : ', no_model: 'Aucun modèle sélectionné',
    api_error: 'Erreur API',
    show_thinking: 'Réflexion', incognito: 'Incognito', thinking_label: 'Réfléchit…',
    active_profile: 'Profil actif', no_profile: '— aucun (local uniquement) —',
    stats_title: 'Stats des backends', stats_host: 'Hôte', stats_active: 'Actifs',
    stats_idle: 'libre', stats_busy: 'occupé',
    queue_waiting: 'En attente...', retry_msg: 'Redemander',
    settings_reserve: 'Mode de réservation', reserve_till_response: "Jusqu'à la fin de la réponse",
    reserve_by_timer: 'Par minuterie', lock_btn_lock: 'Verrouillé', lock_btn_release: 'Libéré',
    lock_busy: "L'hôte est verrouillé", lock_no_remote: 'Sélectionnez un modèle distant',
    tab_rag: 'RAG', rag_project: 'Projet', rag_select_project: '— sélectionner un projet —',
    rag_query_ph: 'Que recherchez-vous ?', rag_results: 'Résultats',
    rag_sources: 'Sources', rag_llm_answer: 'Réponse LLM',
    ask_llm: 'Interroger LLM', search: 'Rechercher',
    tab_settings: 'Paramètres', settings_control: 'Contrôle système',
    settings_auth: 'Authentification', settings_auth_user: 'Nom d\'utilisateur', settings_auth_pass: 'Nouveau mot de passe',
    sys_confirm: 'Confirmer l\'action dangereuse',
    sys_restart: 'Redémarrer vyrii', sys_reboot: 'Redémarrer le PC', sys_shutdown: 'Éteindre le PC',
    settings_connection: 'Connexion',
    settings_backend: 'Backend', settings_timeouts: 'Délais',
    settings_req_timeout: 'Délai de requête (s)', settings_worker_timeout: 'Délai worker (s)',
    settings_defaults: 'Valeurs par défaut', settings_default_model: 'Modèle par défaut',
    settings_lang_default: 'Langue', save: 'Enregistrer', settings_saved: 'Enregistré !',
    tab_profile: 'Profils', tab_team: 'Équipe',
    profile_new: 'Nouveau', saved_profiles: 'Sauvegardés',
    profile_name: 'Nom', profile_comment: 'Commentaire',
    workers: 'Workers', add_worker: '+ Worker',
    team_profile: 'Profil', team_combine: 'Combiner', team_ctx_mode: 'Contexte',
    team_query_ph: 'Question pour tous les workers…', aspects: 'Aspects (un par worker)',
    run: 'Exécuter', da_use_team: 'Équipe',
    tab_scheduler: 'Planificateur', tab_projects: 'Projets', tab_simargl: 'simargl', tab_svitovyd: 'svitovyd',
    proj_select: 'Projet', proj_add: 'Ajouter un projet', proj_name: 'Nom', proj_path: 'Chemin',
    proj_add_btn: 'Ajouter', sim_index_btn: 'Indexer', sim_search_btn: 'Rechercher',
    svy_index_btn: 'Indexer', svy_run_btn: 'Exécuter', sch_create_btn: 'Créer la tâche',
    sch_toggle_btn: 'Activer/Désactiver', sch_run_now_btn: 'Exécuter maintenant', sch_delete_btn: 'Supprimer',
    run_ok: 'Terminé (code {code}, {dur}s)', run_error: 'Erreur (code {code})',
    sim_tab_help: 'Tu as une tâche. Tu ne sais pas quels fichiers modifier. simargl lit l\'historique git et trouve les fichiers les plus probables.',
    sim_index_help: 'Lit tous les commits git de ce projet. Construit un index sémantique. À faire une fois avant de chercher.',
    sim_store_help: 'Dossier où l\'index est sauvegardé. Garde la valeur par défaut sauf si tu as une raison de changer.',
    sim_search_help: 'Écris ce que tu veux faire — comme un titre Jira. simargl trouve les fichiers les plus susceptibles de changer.',
    sim_topk_help: 'Combien de résultats afficher.',
    sim_mode_label: 'Mode', sim_mode_help: 'file — recherche par contenu. aggr — groupé par module (pour requêtes vagues). task — fichiers via commits git (meilleur pour bugs précis). refine — enrichit la requête avec les termes du projet depuis les commits (quand le vocabulaire est inconnu).',
    sim_sort_label: 'Tri', sim_sort_help: 'rank — par score. freq — par fréquence (task seulement).',
    sim_target_help: 'file — fichiers. aggr — dossiers/packages. task — tâches similaires avec fichiers modifiés.',
    svy_tab_help: 'Scanne le code et construit une carte de toutes les fonctions, classes et leurs liens.',
    svy_index_help: 'Scanne le dossier du projet. Trouve toutes les définitions de fonctions et classes. Sauvegarde les liens. Résultat: .svitovyd/map.txt.',
    svy_depth_help: '2 — scanne les définitions et appels. 3 — aussi les variables et paramètres. Pour la plupart des projets, 2 suffit.',
    svy_find_help: 'Filtre la carte par mot-clé.\nExemples:\n  auth — trouve les blocs avec "auth"\n  auth !test — trouve "auth", ignore "test"\n  \\insertUser — trouve exactement "insertUser"',
    svy_trace_help: 'Entre un nom de fonction ou de classe. Vois qui l\'appelle. Utile quand tu veux savoir: qu\'est-ce qui casse si je change ça?',
    svy_deps_help: 'Entre un nom de fonction ou de classe. Vois ce qu\'elle appelle. Utile quand tu veux savoir: de quoi ça dépend?',
    svy_sym_help: 'Trouve les fonctions que beaucoup appellent, mais qui appellent peu. Souvent bons candidats pour le refactoring.',
    svy_kw_help: 'Sans texte — liste les identifiants les plus utilisés. Avec texte — trouve les identifiants liés à ce texte.',
    svy_kw_fuzzy_help: 'Sépare camelCase et snake_case. Trouve plus de correspondances. Exemple: "user" trouve "getUserById".',
    svy_idiff_help: 'Compare deux instantanés de carte. Montre ce qui a changé. Utile après un grand refactoring.',
    svy_idiff_prev_help: 'Chemin vers l\'ancien fichier de carte. Copie .svitovyd/map.txt avant le refactoring, puis compare après.',
    sch_tasks: 'Tâches', sch_add_task: 'Nouvelle tâche',
    sch_name_label: 'Nom', sch_command_label: 'Commande', sch_stype_label: 'Type',
    sch_time_label: 'Heure HH:MM', sch_dow_label: 'Jour', sch_interval_label: 'Intervalle',
    sch_task_id_label: 'ID tâche (8 premiers car.)',
    sch_load_logs_btn: 'Charger les logs', scheduler_logs_section: 'Voir les logs',
    sch_name_placeholder: 'Crawl matinal', sch_command_placeholder: 'simargl index files .',
    projects_desc: 'Registre de projets — nom vers chemin local. Utilisé par simargl et svitovyd.',
    proj_desc_label: 'Description (optionnelle)', proj_delete_confirm: 'Supprimer le projet ?',
    simargl_desc: 'Recherche code par tâche — indexer un projet, puis chercher par description.',
    sim_index_desc: 'Indexe le projet avec simargl. Crée un index sémantique dans ~/.vyrii/.simargl/<project>/.',
    sim_store: 'Dossier', sim_query: 'Description de tâche',
    sim_query_ph: 'Corriger fuite mémoire dans le pool de connexions…',
    sim_top_k: 'Top K', sim_target: 'Cible',
    svitovyd_desc: 'Carte du projet — indexer la structure, puis trouver/tracer/dépendances.',
    svy_index_desc: 'Scanne le répertoire et écrit .svitovyd/map.txt.',
    svy_depth: 'Profondeur', svy_find_query: 'Termes de recherche',
    svy_identifier: 'Identifiant', svy_depth_label: 'Profondeur',
    svy_top_k: 'Top K', svy_kw_task: 'Texte de tâche (optionnel — extraction)',
    svy_kw_fuzzy: 'Fuzzy', svy_idiff_prev: 'Chemin ancien fichier carte',
    sim_topn_help: 'Nombre total de résultats à retourner.',
    sim_format_label: 'Format',
    sim_format_help: 'text — lisible. paths — chemins seulement. modules — noms de modules. json — JSON brut.',
    sim_diff_help: 'Inclure les extraits de code modifiés dans les résultats.',
    sim_noblackholes_help: 'Exclure les fichiers présents dans presque chaque tâche (bruit).',
    sim_stderr_help: 'Afficher stderr dans le résultat. Désactivé par défaut. Activer pour déboguer.',
    sim_rrf_btn: 'Recherche RRF', sim_rrf_sources_label: 'Sources',
    sim_rrf_sources_help: 'Paires mode:projet séparées par virgule. Les fichiers dans plusieurs sources montent dans le classement.',
    sim_rrf_topk_help: 'Candidats par source avant la fusion.',
    sim_rrf_k_help: 'Constante d\'amortissement (défaut 60).',
    sim_blend_help: '0.7 abaisse les fichiers larges (changelog). 1.0 = désactivé.',
    sim_rrf_help: 'Lance plusieurs recherches et fusionne par position. Un fichier présent dans task et file monte automatiquement.',
    // Prompts
    tab_prompts: 'Prompts', prompts_desc: 'Bibliothèque de prompts — sauvegarder et rechercher par nom, modèle ou domaine.',
    prompts_filter_ph: 'Filtrer par nom, modèle, domaine…',
    prm_add: 'Ajouter un prompt', prm_name: 'Nom', prm_desc_label: 'Description',
    prm_model_label: 'Modèle', prm_area_label: 'Domaine', prm_prompt_label: 'Texte du prompt',
    prm_add_btn: 'Enregistrer', prm_none: 'Aucun prompt encore',
  },
  es: {
    logo: 'V Y R I I', tagline: 'herramientas IA locales',
    tab_chat: 'Chat', tab_translate: 'Traducir', tab_obfuscate: 'Ofuscar',
    tab_files: 'Archivos', tab_webcrawl: 'WebCrawl', tab_webanalys: 'WebAnálisis',
    tab_deepagent: 'DeepAgent', tab_scan: 'Escanear', tab_webindex: 'WebÍndice',
    label_model: 'Modelo', label_theme: 'Tema', label_lang: 'Idioma',
    new_chat: 'Nuevo chat', compact_chat: 'Compactar', clear: 'Limpiar', send: 'Enviar', stop: 'Parar',
    compacting: 'Compactando…', compacted_ok: 'Conversación compactada',
    history: 'Historial', hist_search_ph: 'Buscar conversaciones…',
    hist_empty: 'Sin conversaciones aún',
    generating: 'Generando…', chat_empty: 'Comience una conversación…',
    message_ph: 'Mensaje… (Mayús+Intro para enviar)',
    from_lang: 'De', to_lang: 'A', mode: 'Modo',
    source_text: 'Texto fuente', translation: 'Traducción',
    translate_btn: 'Traducir', source_ph: 'Texto a traducir…',
    question: 'Pregunta', question_ph: '¿Qué desea saber?',
    top_n: 'Top N', ask: 'Preguntar',
    task: 'Tarea', task_ph: 'Resumir el contenido principal…',
    wc_mode: 'Modo', wc_filter: 'Filtro', wc_ask: 'Resumen LLM',
    wc_format: 'Formato de salida', wc_columns: 'Columnas (YAML / XPath)',
    max_pages: 'Páginas máx.', crawl: 'Rastrear',
    query: 'Consulta', query_ph: 'Tema de búsqueda…', results: 'Resultados', analyze: 'Analizar',
    sections: 'Secciones', generate: 'Generar',
    da_use_web: 'Búsqueda web', da_web_n: 'Resultados/sección',
    da_use_rag: 'RAG',
    da_task_ph: 'Describa el documento que desea generar…',
    path: 'Ruta', query_optional: 'Consulta (opcional)',
    sc_query_ph: 'Filtrar por tema…',
    chunk: 'Chunk', summary_size: 'Resumen', target: 'Objetivo', rounds: 'Rondas',
    extensions: 'Extensiones', compact: 'Escanear',
    project_name: 'Nombre del proyecto', output_path: 'Ruta de salida', depth: 'Profundidad', index: 'Indexar',
    obfuscate_title: 'Ofuscar', deobfuscate_title: 'Desofuscar',
    text: 'Texto', glossary: 'Nombre del glosario', force_mode: 'Forzado',
    obfuscate_btn: 'Ofuscar', deobfuscate_btn: 'Decodificar',
    of_ph: 'Texto a ofuscar…', dof_ph: 'Texto ofuscado a decodificar…',
    optional: '(opcional)',
    refresh: 'Actualizar', new_folder: 'Nueva carpeta', upload: 'Subir',
    create: 'Crear', cancel: 'Cancelar', delete_btn: 'Eliminar',
    view: 'Ver', scan_btn: 'Escanear', index_btn: 'Indexar',
    mkdir_ph: 'carpeta/nombre', select_file: 'Seleccionar archivo o carpeta',
    loading: 'Cargando…',
    result_here: 'El resultado aparecerá aquí…',
    copy: 'Copiar', copy_raw: 'Copiar markdown', copy_fmt: 'Copiar con formato', copied: '¡Copiado!', add_to_chat: 'Añadir al chat',
    ctx_received: 'Contexto recibido. ¿Qué desea saber?',
    ctx_added: 'Añadido al contexto del chat',
    login_btn: 'Iniciar sesión', login_error: 'Credenciales inválidas', logout_btn: 'Cerrar sesión',
    error_prefix: 'Error: ', no_model: 'Sin modelo seleccionado',
    api_error: 'Error de API',
    show_thinking: 'Pensando', incognito: 'Incógnito', thinking_label: 'Pensando…',
    active_profile: 'Perfil activo', no_profile: '— ninguno (solo local) —',
    stats_title: 'Estadísticas de backends', stats_host: 'Host', stats_active: 'Activos',
    stats_idle: 'libre', stats_busy: 'ocupado',
    queue_waiting: 'Esperando en cola...', retry_msg: 'Preguntar de nuevo',
    settings_reserve: 'Modo de reserva', reserve_till_response: 'Hasta fin de respuesta',
    reserve_by_timer: 'Por temporizador', lock_btn_lock: 'Bloqueado', lock_btn_release: 'Liberado',
    lock_busy: 'Host bloqueado', lock_no_remote: 'Seleccione un modelo remoto',
    tab_rag: 'RAG', rag_project: 'Proyecto', rag_select_project: '— seleccionar proyecto —',
    rag_query_ph: '¿Qué está buscando?', rag_results: 'Resultados',
    rag_sources: 'Fuentes', rag_llm_answer: 'Respuesta LLM',
    ask_llm: 'Preguntar a LLM', search: 'Buscar',
    tab_settings: 'Configuración', settings_control: 'Control del sistema',
    settings_auth: 'Autenticación', settings_auth_user: 'Usuario', settings_auth_pass: 'Nueva contraseña',
    sys_confirm: 'Confirmar acción peligrosa',
    sys_restart: 'Reiniciar vyrii', sys_reboot: 'Reiniciar PC', sys_shutdown: 'Apagar PC',
    settings_connection: 'Conexión',
    settings_backend: 'Backend', settings_timeouts: 'Tiempos de espera',
    settings_req_timeout: 'Tiempo de espera de solicitud (s)', settings_worker_timeout: 'Tiempo de espera worker (s)',
    settings_defaults: 'Valores por defecto', settings_default_model: 'Modelo por defecto',
    settings_lang_default: 'Idioma', save: 'Guardar', settings_saved: '¡Guardado!',
    tab_profile: 'Perfiles', tab_team: 'Equipo',
    profile_new: 'Nuevo', saved_profiles: 'Guardados',
    profile_name: 'Nombre', profile_comment: 'Comentario',
    workers: 'Workers', add_worker: '+ Worker',
    team_profile: 'Perfil', team_combine: 'Combinar', team_ctx_mode: 'Contexto',
    team_query_ph: 'Pregunta para todos los workers…', aspects: 'Aspectos (uno por worker)',
    run: 'Ejecutar', da_use_team: 'Equipo',
    tab_scheduler: 'Planificador', tab_projects: 'Proyectos', tab_simargl: 'simargl', tab_svitovyd: 'svitovyd',
    proj_select: 'Proyecto', proj_add: 'Añadir proyecto', proj_name: 'Nombre', proj_path: 'Ruta',
    proj_add_btn: 'Añadir', sim_index_btn: 'Indexar', sim_search_btn: 'Buscar',
    svy_index_btn: 'Indexar', svy_run_btn: 'Ejecutar', sch_create_btn: 'Crear tarea',
    sch_toggle_btn: 'Activar/Desactivar', sch_run_now_btn: 'Ejecutar ahora', sch_delete_btn: 'Eliminar',
    run_ok: 'Listo (código {code}, {dur}s)', run_error: 'Error (código {code})',
    sim_tab_help: 'Tienes una tarea. No sabes qué archivos cambiar. simargl lee el historial git y encuentra los archivos más probables.',
    sim_index_help: 'Lee todos los commits git de este proyecto. Crea un índice semántico. Hazlo una vez antes de buscar.',
    sim_store_help: 'Carpeta donde se guarda el índice. Usa el valor por defecto a menos que tengas una razón para cambiarlo.',
    sim_search_help: 'Escribe lo que quieres hacer — como un título de Jira. simargl encuentra los archivos con más probabilidad de cambiar.',
    sim_topk_help: 'Cuántos resultados mostrar.',
    sim_mode_label: 'Modo', sim_mode_help: 'file — busca por contenido. aggr — agrupado por módulo (para consultas vagas). task — archivos via commits git (mejor para bugs específicos). refine — amplía la consulta con términos del proyecto de los commits (cuando no conoces el vocabulario).',
    sim_sort_label: 'Orden', sim_sort_help: 'rank — por puntuación. freq — por frecuencia (solo task).',
    sim_target_help: 'file — archivos. aggr — carpetas/paquetes. task — tareas similares con archivos cambiados.',
    svy_tab_help: 'Escanea el código y crea un mapa de todas las funciones, clases y sus vínculos.',
    svy_index_help: 'Escanea la carpeta del proyecto. Encuentra todas las definiciones de funciones y clases. Guarda los vínculos. Resultado: .svitovyd/map.txt.',
    svy_depth_help: '2 — escanea definiciones y llamadas. 3 — también variables y parámetros. Para la mayoría de proyectos, 2 es suficiente.',
    svy_find_help: 'Filtra el mapa por palabra clave.\nEjemplos:\n  auth — encuentra bloques con "auth"\n  auth !test — encuentra "auth", omite "test"\n  \\insertUser — encuentra exactamente "insertUser"',
    svy_trace_help: 'Escribe un nombre de función o clase. Ve quién la llama. Útil cuando quieres saber: ¿qué se rompe si cambio esto?',
    svy_deps_help: 'Escribe un nombre de función o clase. Ve qué llama. Útil cuando quieres saber: ¿de qué depende esto?',
    svy_sym_help: 'Encuentra funciones que muchos llaman pero que llaman pocas. A menudo buenos candidatos para refactorizar.',
    svy_kw_help: 'Sin texto — lista los identificadores más usados. Con texto — encuentra identificadores relacionados con ese texto.',
    svy_kw_fuzzy_help: 'Separa camelCase y snake_case. Encuentra más coincidencias. Ejemplo: "user" encuentra "getUserById".',
    svy_idiff_help: 'Compara dos instantáneas del mapa. Muestra qué cambió. Útil después de un gran refactoring.',
    svy_idiff_prev_help: 'Ruta al archivo de mapa antiguo. Copia .svitovyd/map.txt antes del refactoring, luego compara después.',
    sch_tasks: 'Tareas', sch_add_task: 'Nueva tarea',
    sch_name_label: 'Nombre', sch_command_label: 'Comando', sch_stype_label: 'Tipo',
    sch_time_label: 'Hora HH:MM', sch_dow_label: 'Día', sch_interval_label: 'Intervalo',
    sch_task_id_label: 'ID tarea (8 primeros car.)',
    sch_load_logs_btn: 'Cargar logs', scheduler_logs_section: 'Ver logs',
    sch_name_placeholder: 'Rastreo matutino', sch_command_placeholder: 'simargl index files .',
    projects_desc: 'Registro de proyectos — nombre a ruta local. Usado por simargl y svitovyd.',
    proj_desc_label: 'Descripción (opcional)', proj_delete_confirm: '¿Eliminar proyecto?',
    simargl_desc: 'Búsqueda de código por tarea — indexar un proyecto, luego buscar por descripción.',
    sim_index_desc: 'Indexa el proyecto con simargl. Crea un índice semántico en ~/.vyrii/.simargl/<project>/.',
    sim_store: 'Carpeta', sim_query: 'Descripción de tarea',
    sim_query_ph: 'Corregir fuga de memoria en el pool de conexiones…',
    sim_top_k: 'Top K', sim_target: 'Objetivo',
    svitovyd_desc: 'Mapa del proyecto — indexar estructura, luego encontrar/rastrear/dependencias.',
    svy_index_desc: 'Escanea el directorio del proyecto y escribe .svitovyd/map.txt.',
    svy_depth: 'Profundidad', svy_find_query: 'Términos de búsqueda',
    svy_identifier: 'Identificador', svy_depth_label: 'Profundidad',
    svy_top_k: 'Top K', svy_kw_task: 'Texto de tarea (opcional — extracción)',
    svy_kw_fuzzy: 'Fuzzy', svy_idiff_prev: 'Ruta del archivo mapa anterior',
    sim_topn_help: 'Número total de resultados a devolver.',
    sim_format_label: 'Formato',
    sim_format_help: 'text — legible. paths — solo rutas. modules — solo módulos. json — JSON crudo.',
    sim_diff_help: 'Incluir fragmentos de código modificados en los resultados.',
    sim_noblackholes_help: 'Excluir archivos que aparecen en casi todas las tareas (ruido).',
    sim_stderr_help: 'Mostrar stderr en el resultado. Desactivado por defecto. Activar para depurar.',
    sim_rrf_btn: 'Búsqueda RRF', sim_rrf_sources_label: 'Fuentes',
    sim_rrf_sources_help: 'Pares modo:proyecto separados por coma. Archivos en varias fuentes suben en el ranking.',
    sim_rrf_topk_help: 'Candidatos por fuente antes de la fusión.',
    sim_rrf_k_help: 'Constante de amortiguación (predeterminado 60).',
    sim_blend_help: '0.7 baja archivos amplios (changelog). 1.0 = desactivado.',
    sim_rrf_help: 'Ejecuta varias búsquedas y fusiona por posición. Un archivo en task y file a la vez sube automáticamente.',
    // Prompts
    tab_prompts: 'Prompts', prompts_desc: 'Biblioteca de prompts — guardar y buscar por nombre, modelo o área.',
    prompts_filter_ph: 'Filtrar por nombre, modelo, área…',
    prm_add: 'Añadir prompt', prm_name: 'Nombre', prm_desc_label: 'Descripción',
    prm_model_label: 'Modelo', prm_area_label: 'Área', prm_prompt_label: 'Texto del prompt',
    prm_add_btn: 'Guardar', prm_none: 'Sin prompts aún',
  },
  pt: {
    logo: 'V Y R I I', tagline: 'ferramentas de IA locais',
    tab_chat: 'Chat', tab_translate: 'Traduzir', tab_obfuscate: 'Ofuscar',
    tab_files: 'Arquivos', tab_webcrawl: 'WebCrawl', tab_webanalys: 'WebAnálise',
    tab_deepagent: 'DeepAgent', tab_scan: 'Varredura', tab_webindex: 'WebÍndice',
    label_model: 'Modelo', label_theme: 'Tema', label_lang: 'Idioma',
    new_chat: 'Novo chat', compact_chat: 'Compactar', clear: 'Limpar', send: 'Enviar', stop: 'Parar',
    compacting: 'Compactando…', compacted_ok: 'Conversa compactada',
    history: 'Histórico', hist_search_ph: 'Pesquisar conversas…',
    hist_empty: 'Nenhuma conversa ainda',
    generating: 'Gerando…', chat_empty: 'Inicie uma conversa…',
    message_ph: 'Mensagem… (Shift+Enter para enviar)',
    from_lang: 'De', to_lang: 'Para', mode: 'Modo',
    source_text: 'Texto fonte', translation: 'Tradução',
    translate_btn: 'Traduzir', source_ph: 'Texto a traduzir…',
    question: 'Pergunta', question_ph: 'O que você gostaria de saber?',
    top_n: 'Top N', ask: 'Perguntar',
    task: 'Tarefa', task_ph: 'Resumir o conteúdo principal…',
    wc_mode: 'Modo', wc_filter: 'Filtro', wc_ask: 'Resumo LLM',
    wc_format: 'Formato de saída', wc_columns: 'Colunas (YAML / XPath)',
    max_pages: 'Páginas máx.', crawl: 'Rastrear',
    query: 'Consulta', query_ph: 'Tema de pesquisa…', results: 'Resultados', analyze: 'Analisar',
    sections: 'Seções', generate: 'Gerar',
    da_use_web: 'Pesquisa web', da_web_n: 'Resultados/seção',
    da_use_rag: 'RAG',
    da_task_ph: 'Descreva o documento que deseja gerar…',
    path: 'Caminho', query_optional: 'Consulta (opcional)',
    sc_query_ph: 'Filtrar por tema…',
    chunk: 'Chunk', summary_size: 'Resumo', target: 'Alvo', rounds: 'Rodadas',
    extensions: 'Extensões', compact: 'Varrer',
    project_name: 'Nome do projeto', output_path: 'Caminho de saída', depth: 'Profundidade', index: 'Indexar',
    obfuscate_title: 'Ofuscar', deobfuscate_title: 'Desofuscar',
    text: 'Texto', glossary: 'Nome do glossário', force_mode: 'Forçado',
    obfuscate_btn: 'Ofuscar', deobfuscate_btn: 'Decodificar',
    of_ph: 'Texto a ofuscar…', dof_ph: 'Texto ofuscado a decodificar…',
    optional: '(opcional)',
    refresh: 'Atualizar', new_folder: 'Nova pasta', upload: 'Enviar',
    create: 'Criar', cancel: 'Cancelar', delete_btn: 'Excluir',
    view: 'Visualizar', scan_btn: 'Varrer', index_btn: 'Indexar',
    mkdir_ph: 'pasta/nome', select_file: 'Selecionar arquivo ou pasta',
    loading: 'Carregando…',
    result_here: 'O resultado aparecerá aqui…',
    copy: 'Copiar', copy_raw: 'Copiar markdown', copy_fmt: 'Copiar formatado', copied: 'Copiado!', add_to_chat: 'Adicionar ao chat',
    ctx_received: 'Contexto recebido. O que você gostaria de saber?',
    ctx_added: 'Adicionado ao contexto do chat',
    login_btn: 'Entrar', login_error: 'Credenciais inválidas', logout_btn: 'Sair',
    error_prefix: 'Erro: ', no_model: 'Nenhum modelo selecionado',
    api_error: 'Erro de API',
    show_thinking: 'Pensamento', incognito: 'Anônimo', thinking_label: 'Pensando…',
    active_profile: 'Perfil ativo', no_profile: '— nenhum (somente local) —',
    stats_title: 'Estatísticas de backends', stats_host: 'Host', stats_active: 'Ativos',
    stats_idle: 'livre', stats_busy: 'ocupado',
    queue_waiting: 'Aguardando na fila...', retry_msg: 'Perguntar novamente',
    settings_reserve: 'Modo de reserva', reserve_till_response: 'Até o fim da resposta',
    reserve_by_timer: 'Por temporizador', lock_btn_lock: 'Bloqueado', lock_btn_release: 'Liberado',
    lock_busy: 'Host bloqueado', lock_no_remote: 'Selecione um modelo remoto',
    tab_rag: 'RAG', rag_project: 'Projeto', rag_select_project: '— selecionar projeto —',
    rag_query_ph: 'O que você está procurando?', rag_results: 'Resultados',
    rag_sources: 'Fontes', rag_llm_answer: 'Resposta LLM',
    ask_llm: 'Perguntar ao LLM', search: 'Pesquisar',
    tab_settings: 'Configurações', settings_control: 'Controle do sistema',
    settings_auth: 'Autenticação', settings_auth_user: 'Nome de usuário', settings_auth_pass: 'Nova senha',
    sys_confirm: 'Confirmar ação perigosa',
    sys_restart: 'Reiniciar vyrii', sys_reboot: 'Reiniciar PC', sys_shutdown: 'Desligar PC',
    settings_connection: 'Conexão',
    settings_backend: 'Backend', settings_timeouts: 'Tempos limite',
    settings_req_timeout: 'Tempo limite de solicitação (s)', settings_worker_timeout: 'Tempo limite worker (s)',
    settings_defaults: 'Padrões', settings_default_model: 'Modelo padrão',
    settings_lang_default: 'Idioma', save: 'Salvar', settings_saved: 'Salvo!',
    tab_profile: 'Perfis', tab_team: 'Equipe',
    profile_new: 'Novo', saved_profiles: 'Salvos',
    profile_name: 'Nome', profile_comment: 'Comentário',
    workers: 'Workers', add_worker: '+ Worker',
    team_profile: 'Perfil', team_combine: 'Combinar', team_ctx_mode: 'Contexto',
    team_query_ph: 'Pergunta para todos os workers…', aspects: 'Aspectos (um por worker)',
    run: 'Executar', da_use_team: 'Equipe',
    tab_scheduler: 'Agendador', tab_projects: 'Projetos', tab_simargl: 'simargl', tab_svitovyd: 'svitovyd',
    proj_select: 'Projeto', proj_add: 'Adicionar projeto', proj_name: 'Nome', proj_path: 'Caminho',
    proj_add_btn: 'Adicionar', sim_index_btn: 'Indexar', sim_search_btn: 'Pesquisar',
    svy_index_btn: 'Indexar', svy_run_btn: 'Executar', sch_create_btn: 'Criar tarefa',
    sch_toggle_btn: 'Ativar/Desativar', sch_run_now_btn: 'Executar agora', sch_delete_btn: 'Excluir',
    run_ok: 'Concluído (código {code}, {dur}s)', run_error: 'Erro (código {code})',
    sim_tab_help: 'Você tem uma tarefa. Não sabe quais arquivos mudar. simargl lê o histórico git e encontra os arquivos mais prováveis.',
    sim_index_help: 'Lê todos os commits git deste projeto. Cria um índice semântico. Faça isso uma vez antes de pesquisar.',
    sim_store_help: 'Pasta onde o índice é salvo. Use o padrão a menos que tenha uma razão para mudar.',
    sim_search_help: 'Escreva o que quer fazer — como um título de Jira. simargl encontra os arquivos com mais probabilidade de mudar.',
    sim_topk_help: 'Quantos resultados mostrar.',
    sim_mode_label: 'Modo', sim_mode_help: 'file — busca por conteúdo. aggr — agrupado por módulo (para consultas vagas). task — arquivos via commits git (melhor para bugs específicos). refine — amplia a consulta com termos do projeto dos commits (quando não conhece o vocabulário).',
    sim_sort_label: 'Ordenar', sim_sort_help: 'rank — por pontuação. freq — por frequência (só task).',
    sim_target_help: 'file — arquivos. aggr — pastas/pacotes. task — tarefas similares com arquivos alterados.',
    svy_tab_help: 'Escaneia o código e cria um mapa de todas as funções, classes e seus vínculos.',
    svy_index_help: 'Escaneia a pasta do projeto. Encontra todas as definições de funções e classes. Salva os vínculos. Resultado: .svitovyd/map.txt.',
    svy_depth_help: '2 — escaneia definições e chamadas. 3 — também variáveis e parâmetros. Para a maioria dos projetos, 2 é suficiente.',
    svy_find_help: 'Filtra o mapa por palavra-chave.\nExemplos:\n  auth — encontra blocos com "auth"\n  auth !test — encontra "auth", ignora "test"\n  \\insertUser — encontra exatamente "insertUser"',
    svy_trace_help: 'Digite um nome de função ou classe. Veja quem a chama. Útil quando quer saber: o que quebra se eu mudar isso?',
    svy_deps_help: 'Digite um nome de função ou classe. Veja o que ela chama. Útil quando quer saber: do que isso depende?',
    svy_sym_help: 'Encontra funções que muitos chamam mas que chamam poucas. Frequentemente bons candidatos para refatoração.',
    svy_kw_help: 'Sem texto — lista os identificadores mais usados. Com texto — encontra identificadores relacionados a esse texto.',
    svy_kw_fuzzy_help: 'Separa camelCase e snake_case. Encontra mais correspondências. Exemplo: "user" encontra "getUserById".',
    svy_idiff_help: 'Compara dois instantâneos do mapa. Mostra o que mudou. Útil após uma grande refatoração.',
    svy_idiff_prev_help: 'Caminho para o arquivo de mapa antigo. Copie .svitovyd/map.txt antes da refatoração, depois compare.',
    sch_tasks: 'Tarefas', sch_add_task: 'Nova tarefa',
    sch_name_label: 'Nome', sch_command_label: 'Comando', sch_stype_label: 'Tipo',
    sch_time_label: 'Hora HH:MM', sch_dow_label: 'Dia', sch_interval_label: 'Intervalo',
    sch_task_id_label: 'ID tarefa (8 primeiros car.)',
    sch_load_logs_btn: 'Carregar logs', scheduler_logs_section: 'Ver logs',
    sch_name_placeholder: 'Rastreamento matinal', sch_command_placeholder: 'simargl index files .',
    projects_desc: 'Registro de projetos — nome para caminho local. Usado por simargl e svitovyd.',
    proj_desc_label: 'Descrição (opcional)', proj_delete_confirm: 'Excluir projeto?',
    simargl_desc: 'Busca de código por tarefa — indexar um projeto, então pesquisar por descrição.',
    sim_index_desc: 'Indexa o projeto com simargl. Cria um índice semântico em ~/.vyrii/.simargl/<project>/.',
    sim_store: 'Pasta', sim_query: 'Descrição da tarefa',
    sim_query_ph: 'Corrigir vazamento de memória no pool de conexões…',
    sim_top_k: 'Top K', sim_target: 'Alvo',
    svitovyd_desc: 'Mapa do projeto — indexar estrutura, depois encontrar/rastrear/dependências.',
    svy_index_desc: 'Escaneia o diretório do projeto e escreve .svitovyd/map.txt.',
    svy_depth: 'Profundidade', svy_find_query: 'Termos de busca',
    svy_identifier: 'Identificador', svy_depth_label: 'Profundidade',
    svy_top_k: 'Top K', svy_kw_task: 'Texto da tarefa (opcional — extração)',
    svy_kw_fuzzy: 'Fuzzy', svy_idiff_prev: 'Caminho do arquivo mapa anterior',
    sim_topn_help: 'Total de resultados a retornar.',
    sim_format_label: 'Formato',
    sim_format_help: 'text — legível. paths — só caminhos. modules — só módulos. json — JSON bruto.',
    sim_diff_help: 'Incluir trechos de código alterados nos resultados.',
    sim_noblackholes_help: 'Excluir arquivos que aparecem em quase todas as tarefas (ruído).',
    sim_stderr_help: 'Mostrar stderr no resultado. Desativado por padrão. Ativar para depurar erros.',
    sim_rrf_btn: 'Busca RRF', sim_rrf_sources_label: 'Fontes',
    sim_rrf_sources_help: 'Pares modo:projeto separados por vírgula. Arquivos em várias fontes sobem no ranking.',
    sim_rrf_topk_help: 'Candidatos por fonte antes da fusão.',
    sim_rrf_k_help: 'Constante de amortecimento (padrão 60).',
    sim_blend_help: '0.7 rebaixa arquivos amplos (changelog). 1.0 = desativado.',
    sim_rrf_help: 'Executa várias buscas e funde por posição. Um arquivo em task e file ao mesmo tempo sobe automaticamente.',
    // Prompts
    tab_prompts: 'Prompts', prompts_desc: 'Biblioteca de prompts — salvar e pesquisar por nome, modelo ou área.',
    prompts_filter_ph: 'Filtrar por nome, modelo, área…',
    prm_add: 'Adicionar prompt', prm_name: 'Nome', prm_desc_label: 'Descrição',
    prm_model_label: 'Modelo', prm_area_label: 'Área', prm_prompt_label: 'Texto do prompt',
    prm_add_btn: 'Salvar', prm_none: 'Nenhum prompt ainda',
  },
};

// ── STATE ─────────────────────────────────────────────
const state = {
  lang:      localStorage.getItem('lang')  || 'en',
  theme:     localStorage.getItem('theme') || 'ocean',
  model:     localStorage.getItem('model') || '',
  activeTab: 'chat',
  streaming: false,
  abortCtrl:   null,
  chatMessages: [],   // [{role, content}]
  chatId:      null,  // DB chat id (null = not yet saved)
  savedCount:  0,     // how many chatMessages are already persisted
  fileViewRaw: '',    // raw content of currently viewed file
  selectedFile: null,
  showThinking: false,
  smartCtx: true,
  fixedCtx: 4096,
  incognito: false,
};

// ── VIEWPORT HEIGHT (keeps --app-h = actual visible height on mobile) ─────
function _setAppH() {
  document.documentElement.style.setProperty('--app-h', window.innerHeight + 'px');
}
window.addEventListener('resize', _setAppH);
_setAppH();

// ── INIT ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  _setAppH();
  applyTheme(state.theme);
  applyLang(state.lang);
  loadThemes();
  setupTabNav();

  // Shift+Enter submits on all tabs (find nearest btn-primary in same panel)
  document.addEventListener('keydown', e => {
    if (!(e.key === 'Enter' && e.shiftKey)) return;
    const ta = e.target;
    if (!(ta.tagName === 'TEXTAREA' || ta.tagName === 'INPUT')) return;
    if (ta.id === 'chat-input') return; // chat has its own handler
    const panel = ta.closest('.tab-panel, .subtab-panel, .form-row')
               || ta.closest('.panel-body');
    if (!panel) return;
    const btn = panel.querySelector('.btn-primary');
    if (btn) { e.preventDefault(); btn.click(); }
  });
  // Probe auth: if 401 and no stored creds, show login overlay
  const probe = await fetch('/v1/models').catch(() => ({ status: 0 }));
  if (probe.status !== 401) {
    loadModels();
  }
  // If 401, fetch wrapper already showed the overlay
});

// ── THEME ─────────────────────────────────────────────
async function loadThemes() {
  try {
    const res    = await fetch('/vyrii/themes');
    const data   = await res.json();
    const themes = data.themes || ['ocean'];
    const opts   = themes
      .map(n => `<option value="${n}"${n === state.theme ? ' selected' : ''}>${n.charAt(0).toUpperCase() + n.slice(1)}</option>`)
      .join('');
    ['theme-select', 'cfg-theme'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = opts;
    });
  } catch { /* offline — keep default */ }
}

function setTheme(name) {
  state.theme = name;
  localStorage.setItem('theme', name);
  applyTheme(name);
}

function applyTheme(name) {
  const link = document.getElementById('theme-link');
  if (link) link.href = `themes/${name}.css`;
  ['theme-select', 'cfg-theme'].forEach(id => {
    const el = document.getElementById(id);
    if (el && el.value !== name) el.value = name;
  });
}

// ── LANGUAGE ──────────────────────────────────────────
function setLang(l) {
  state.lang = l;
  localStorage.setItem('lang', l);
  applyLang(l);
}

function applyLang(l) {
  const d = I18N[l] || I18N.en;

  // text content
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    if (d[key] !== undefined) el.textContent = d[key];
  });

  // placeholders
  document.querySelectorAll('[data-i18n-ph]').forEach(el => {
    const key = el.dataset.i18nPh;
    if (d[key] !== undefined) el.placeholder = d[key];
  });

  // sync language selects (sidebar + settings tab)
  ['lang-select', 'cfg-lang'].forEach(id => {
    const el = document.getElementById(id);
    if (el && el.value !== l) el.value = l;
  });
}

function t(key) {
  return (I18N[state.lang] || I18N.en)[key] || key;
}

// ── MODELS ────────────────────────────────────────────
async function loadModels() {
  try {
    const res = await fetch('/v1/models');
    const data = await res.json();
    const items = data.data || [];
    const sel = document.getElementById('g-model');

    if (!items.length) {
      sel.innerHTML = '<option value="">— no models —</option>';
      return;
    }

    const groups = {};
    for (const m of items) {
      const g = m.group || 'local';
      if (!groups[g]) groups[g] = [];
      const label = m.id.includes('@') ? m.id.split('@')[0] : m.id;
      groups[g].push({ id: m.id, label });
    }

    const keys = Object.keys(groups);
    if (keys.length === 1) {
      sel.innerHTML = groups[keys[0]]
        .map(m => `<option value="${m.id}">${m.label}</option>`).join('');
    } else {
      sel.innerHTML = keys.map(g =>
        `<optgroup label="${g}">${groups[g]
          .map(m => `<option value="${m.id}">${m.label}</option>`).join('')}</optgroup>`
      ).join('');
    }

    const allIds = items.map(m => m.id);
    if (state.model && allIds.includes(state.model)) {
      sel.value = state.model;
    } else {
      state.model = allIds[0];
      sel.value = allIds[0];
    }
  } catch (e) {
    document.getElementById('g-model').innerHTML = '<option value="">— offline —</option>';
  }
}

function onModelChange() {
  state.model = document.getElementById('g-model').value;
  localStorage.setItem('model', state.model);
}

function getModel() {
  return document.getElementById('g-model').value || state.model;
}

// ── STATS POPUP ──────────────────────────────────────
async function toggleStatsPopup() {
  const popup = document.getElementById('stats-popup');
  if (popup.style.display !== 'none') {
    popup.style.display = 'none';
    return;
  }
  try {
    const res = await fetch('/vyrii/stats');
    const data = await res.json();
    const rows = (data.stats || []);
    if (!rows.length) {
      popup.innerHTML = `<div class="stats-empty">${t('stats_title')}: —</div>`;
    } else {
      const hdr = `<tr><th>${t('stats_host')}</th><th>${t('stats_active')}</th><th>1m</th><th>5m</th><th>15m</th><th></th></tr>`;
      const body = rows.map(r => {
        const busy = r.active > 0;
        const badge = busy
          ? `<span class="stats-badge busy">${t('stats_busy')}</span>`
          : `<span class="stats-badge idle">${t('stats_idle')}</span>`;
        return `<tr><td>${r.host}</td><td>${r.active}</td><td>${r.req_1m}</td><td>${r.req_5m}</td><td>${r.req_15m}</td><td>${badge}</td></tr>`;
      }).join('');
      popup.innerHTML = `<div class="stats-header">${t('stats_title')}</div><table class="stats-table"><thead>${hdr}</thead><tbody>${body}</tbody></table>`;
    }
    popup.style.display = 'block';
  } catch (e) {
    popup.innerHTML = `<div class="stats-empty">${t('error_prefix')}${e.message}</div>`;
    popup.style.display = 'block';
  }
}

document.addEventListener('click', (e) => {
  const popup = document.getElementById('stats-popup');
  if (popup && popup.style.display !== 'none' &&
      !popup.contains(e.target) && !e.target.classList.contains('sf-stats-btn')) {
    popup.style.display = 'none';
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const popup = document.getElementById('stats-popup');
    if (popup) popup.style.display = 'none';
  }
});

// ── LOCK / RESERVE ───────────────────────────────────
function _currentHost() {
  const model = getModel();
  if (model.includes('@')) {
    const rest = model.split('@')[1];
    const m = rest.match(/(?:ollama|openai):\/\/(.+)/);
    return m ? m[1] : '';
  }
  return '';
}

async function toggleLock() {
  const host = _currentHost();
  if (!host) { showToast(t('lock_no_remote')); return; }
  try {
    const info = await (await fetch('/vyrii/lock')).json();
    const cur = (info.locks || {})[host];
    const action = cur ? 'release' : 'lock';
    const res = await fetch('/vyrii/lock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host, action }),
    });
    const data = await res.json();
    const btn = document.getElementById('lock-btn');
    if (action === 'lock') {
      if (data.ok) {
        if (btn) btn.innerHTML = '&#x1F512;';
        showToast(t('lock_btn_lock') + ': ' + host);
      } else {
        showToast(data.error || t('lock_busy'));
      }
    } else {
      if (btn) btn.innerHTML = '&#x1F513;';
      showToast(t('lock_btn_release') + ': ' + host);
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

// ── ACTIVE PROFILE (settings) ────────────────────────
async function loadProfileOptions() {
  const sel = document.getElementById('cfg-active-profile');
  if (!sel) return;
  try {
    const res = await fetch('/vyrii/team/profiles');
    const data = await res.json();
    const profiles = data.profiles || [];
    sel.innerHTML = `<option value="">${t('no_profile')}</option>` +
      profiles.map(p => `<option value="${p.name}">${p.name}</option>`).join('');
  } catch { /* keep default option */ }
}

// ── TAB NAVIGATION ────────────────────────────────────
function setupTabNav() {
  document.querySelectorAll('.nav-item[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tab) {
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

  const navBtn = document.querySelector(`.nav-item[data-tab="${tab}"]`);
  const panel  = document.getElementById(`tab-${tab}`);
  if (navBtn) navBtn.classList.add('active');
  if (panel)  panel.classList.add('active');

  state.activeTab = tab;

  if (tab === 'files' && !state.filesLoaded) {
    state.filesLoaded = true;
    refreshFiles();
  }
  if (tab === 'rag')      ragRefreshProjects();
  if (tab === 'settings') loadSettings();
  if (tab === 'profile')   profileLoad();
  if (tab === 'team')      teamLoadProfiles();
  if (tab === 'scheduler') schRefresh();
  if (tab === 'projects')  projRefresh();
  if (tab === 'simargl')   loadProjectSelects();
  if (tab === 'svitovyd')  loadProjectSelects();
  if (tab === 'prompts')   prmRefresh();
}

// ── MARKDOWN RENDERER ─────────────────────────────────
function md(text) {
  if (!text) return '';

  // Handle <think>...</think> blocks (chain-of-thought from Qwen/DeepSeek)
  if (state.showThinking) {
    text = text.replace(/<think>([\s\S]*?)<\/think>/gi,
      (_, inner) => `\n<details class="thinking-block" open><summary>${t('thinking_label')}</summary>\n\n${inner.trim()}\n\n</details>\n`);
  } else if (state.streaming && /<think>(?![\s\S]*<\/think>)/i.test(text)) {
    text = text.replace(/<think>([\s\S]*)$/i,
      (_, inner) => `\n<details class="thinking-block" open><summary>${t('thinking_label')}</summary>\n\n${inner.trim()}\n\n</details>\n`);
  } else {
    text = text.replace(/<think>[\s\S]*?<\/think>/gi, '');
  }

  // Step 0 — extract math blocks before anything else (LaTeX / KaTeX)
  const mathBlocks = [];
  // display math: $$ ... $$ or \[ ... \]
  let s = text.replace(/\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]/g, (m, a, b) => {
    const idx = mathBlocks.length;
    mathBlocks.push({ display: true, tex: (a ?? b).trim() });
    return `\x00MB${idx}\x00`;
  });
  // inline math: $ ... $ (not $$) or \( ... \)
  s = s.replace(/\$([^\$\n]+?)\$|\\\((.+?)\\\)/g, (m, a, b) => {
    const idx = mathBlocks.length;
    mathBlocks.push({ display: false, tex: (a ?? b).trim() });
    return `\x00MB${idx}\x00`;
  });

  // Step 1a — extract mermaid blocks
  const mermaidBlocks = [];
  s = s.replace(/```mermaid\n?([\s\S]*?)```/g, (_, c) => {
    const idx = mermaidBlocks.length;
    mermaidBlocks.push(c.trim());
    return `\x00MM${idx}\x00`;
  });

  // Step 1b — extract remaining code blocks before escaping
  const codeBlocks = [];
  s = s.replace(/```[\w]*\n?([\s\S]*?)```/g, (_, c) => {
    const idx = codeBlocks.length;
    codeBlocks.push(`<pre><code>${escHtml(c.trim())}</code></pre>`);
    return `\x00CB${idx}\x00`;
  });

  // Step 2 — escape ALL HTML entities in remaining text (prevents <tag> injection)
  s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // Step 3 — inline code
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${escHtml(c)}</code>`);

  // Step 4 — bold / italic
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*\n]+)\*/g,     '<em>$1</em>');

  // Step 5 — headings
  s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
  s = s.replace(/^# (.+)$/gm,   '<h1>$1</h1>');

  // Step 6 — lists
  s = s.replace(/^[*-] (.+)$/gm,    '<li>$1</li>');
  s = s.replace(/^\d+\. (.+)$/gm,   '<li>$1</li>');
  s = s.replace(/(<li>[\s\S]*?<\/li>\n?)+/g, m => `<ul>${m}</ul>`);

  // Step 7 — paragraphs (double newline)
  s = s.split(/\n{2,}/)
    .map(para => para.startsWith('<') ? para : `<p>${para.replace(/\n/g, '<br>')}</p>`)
    .join('');

  // Step 8 — restore code blocks
  s = s.replace(/\x00CB(\d+)\x00/g, (_, i) => codeBlocks[+i]);

  // Step 9 — render math blocks via KaTeX
  s = s.replace(/\x00MB(\d+)\x00/g, (_, i) => {
    const mb = mathBlocks[+i];
    const encoded = mb.tex.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    const tag = mb.display ? 'div' : 'span';
    try {
      const html = katex.renderToString(mb.tex, { displayMode: mb.display, throwOnError: false });
      return `<${tag} class="katex-wrap${mb.display ? ' katex-display-wrap' : ''}" data-tex="${encoded}">${html}<button class="katex-copy" onclick="copyKatexSrc(this)" title="Copy LaTeX">&#128203;</button></${tag}>`;
    } catch { return escHtml(mb.tex); }
  });

  // Step 10 — insert mermaid placeholders (rendered async after innerHTML)
  s = s.replace(/\x00MM(\d+)\x00/g, (_, i) => {
    const raw = mermaidBlocks[+i];
    const encoded = raw.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    return `<div class="mermaid-wrap"><button class="mermaid-copy" onclick="copyMermaidSrc(this)" title="Copy source">&#128203;</button><pre class="mermaid" data-src="${encoded}">${escHtml(raw)}</pre></div>`;
  });

  return s;
}

function copyMsgRaw(idx) {
  const msg = state.chatMessages[idx];
  if (!msg) return;
  navigator.clipboard.writeText(msg.content)
    .then(() => showToast(t('copied')))
    .catch(() => {
      const ta = document.createElement('textarea');
      ta.value = msg.content; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); document.body.removeChild(ta);
      showToast(t('copied'));
    });
}

function copyMsgFmt(idx) {
  const el = document.querySelector(`#msg-${idx} .bubble`);
  if (!el) return;
  const html = el.innerHTML;
  const plain = el.innerText;
  if (navigator.clipboard && typeof ClipboardItem !== 'undefined') {
    const item = new ClipboardItem({
      'text/html':  new Blob([html],  { type: 'text/html' }),
      'text/plain': new Blob([plain], { type: 'text/plain' })
    });
    navigator.clipboard.write([item])
      .then(() => showToast(t('copied')))
      .catch(() => _fallbackCopyFmt(el));
  } else {
    _fallbackCopyFmt(el);
  }
}

function _fallbackCopyFmt(el) {
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges(); sel.addRange(range);
  document.execCommand('copy');
  sel.removeAllRanges();
  showToast(t('copied'));
}

function copyKatexSrc(btn) {
  const wrap = btn.parentElement;
  const src = wrap?.dataset.tex || '';
  navigator.clipboard.writeText(src).then(() => showToast(t('copied')));
}

function copyMermaidSrc(btn) {
  const pre = btn.parentElement.querySelector('pre.mermaid');
  const src = pre?.dataset.src || pre?.textContent || '';
  navigator.clipboard.writeText(src).then(() => showToast(t('copied')));
}

function retryMsg(idx) {
  const msg = state.chatMessages[idx];
  if (!msg || msg.role !== 'assistant' || state.streaming) return;
  state.chatMessages.splice(idx, 1);
  state.savedCount = Math.min(state.savedCount, idx);
  renderChatMessages();
  const prev = state.chatMessages[idx - 1];
  if (prev && prev.role === 'user') {
    document.getElementById('chat-input').value = prev.content;
    sendChat();
  }
}

function renderMermaid(container) {
  if (typeof mermaid === 'undefined') return;
  const nodes = (container || document).querySelectorAll('pre.mermaid:not([data-processed])');
  if (!nodes.length) return;
  try { mermaid.run({ nodes }); } catch {}
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── UI HELPERS ────────────────────────────────────────
function setResult(id, html, isHtml = false) {
  const el = document.getElementById(id);
  if (!el) return;
  if (isHtml) {
    el.innerHTML = html;
  } else {
    el.textContent = html;
  }
}

function setResultMd(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = md(text);
  renderMermaid(el);
}

function setResultLoading(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = `<span class="status-bar"><span class="status-dot"></span>${t('generating')}</span>`;
}

function showToast(msg, duration = 2500) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), duration);
}

function copyResult(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const text = el.innerText || el.textContent;
  navigator.clipboard.writeText(text).then(() => showToast(t('copied')));
}

function addToChat(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const text = (el.innerText || el.textContent).trim();
  if (!text) return;

  // derive label from the enclosing tab's title
  const panel = el.closest('.tab-panel');
  const title = panel ? (panel.querySelector('.ph-title')?.textContent.trim() || '') : '';
  const content = title ? `[${title}]\n${text}` : text;

  // inject as user + assistant pair directly into chat history (same as Gradio)
  state.chatMessages.push({ role: 'user',      content });
  state.chatMessages.push({ role: 'assistant', content: t('ctx_received') });

  switchTab('chat');
  renderChatMessages();
  const box = document.getElementById('chat-messages');
  if (box) box.scrollTop = box.scrollHeight;
  showToast(t('ctx_added'));
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

// ── CHAT ──────────────────────────────────────────────
function chatKeyDown(e) {
  if (e.key === 'Enter' && e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
  // Enter alone → new line (default textarea behaviour, no override needed)
}

function updateCtxIndicator() {
  const el = document.getElementById('ctx-indicator');
  if (!el) return;
  const total = state.chatMessages.reduce((s, m) => s + (m.content?.length || 0), 0);
  const tokens = Math.max(1, Math.round(total / 3));
  if (state.smartCtx) {
    let ctx = 2048;
    while (tokens >= ctx * 0.7) ctx += 2048;
    el.textContent = `~${tokens} / ${ctx} auto`;
  } else {
    el.textContent = `~${tokens} / ${state.fixedCtx} fixed`;
  }
  const inp = document.getElementById('ctx-fixed-input');
  if (inp) inp.style.display = state.smartCtx ? 'none' : '';
}

function toggleCtxMode() {
  state.smartCtx = !state.smartCtx;
  updateCtxIndicator();
}

function toggleIncognito(on) {
  state.incognito = on;
  const label = document.querySelector('.chk-incognito');
  if (label) label.style.color = on ? '#f5a623' : '';
  if (on && state.chatId) {
    fetch(`/vyrii/history/chats/${state.chatId}`, { method: 'DELETE' }).catch(() => {});
    state.chatId = null;
    state.savedCount = 0;
  }
}

function newChat() {
  state.chatMessages = [];
  state.chatId     = null;
  state.savedCount = 0;
  renderChatMessages();
  updateCtxIndicator();
}

function clearChat() { newChat(); }

async function compactChat() {
  if (state.chatMessages.length < 2) return;
  const model = getModel();
  if (!model) { showToast(t('no_model')); return; }
  const btn = document.getElementById('btn-compact');
  if (btn) btn.disabled = true;
  showToast(t('compacting'));
  try {
    const resp = await fetch('/vyrii/compact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: state.chatMessages, model }),
    });
    const data = await resp.json();
    if (!data.summary) { showToast(t('error_prefix') + (data.error || '?')); return; }
    // start new chat with compact summary
    state.chatMessages = [
      { role: 'user',      content: '[Compacted conversation summary]\n\n' + data.summary },
      { role: 'assistant', content: t('compacted_ok') },
    ];
    state.chatId     = null;
    state.savedCount = 0;
    renderChatMessages();
    updateCtxIndicator();
    showToast(t('compacted_ok'));
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── CHAT HISTORY ──────────────────────────────────────
function toggleHistory() {
  const panel = document.getElementById('chat-hist-panel');
  const opening = !panel.classList.contains('open');
  panel.classList.toggle('open');
  if (opening) {
    document.getElementById('chp-search').value = '';
    loadHistoryList('');
  }
}

let _histTimer = null;
function searchHistory(q) {
  clearTimeout(_histTimer);
  _histTimer = setTimeout(() => loadHistoryList(q), 280);
}

async function loadHistoryList(q = '') {
  const list = document.getElementById('chp-list');
  list.innerHTML = `<div class="placeholder-text" style="padding:16px 12px">${t('loading')}</div>`;
  try {
    const url = q.trim()
      ? '/vyrii/history/search?q=' + encodeURIComponent(q.trim())
      : '/vyrii/history/chats';
    const data = await (await fetch(url)).json();
    if (!data.length) {
      list.innerHTML = `<div class="placeholder-text" style="padding:16px 12px">${t('hist_empty')}</div>`;
      return;
    }
    list.innerHTML = data.map(ch => {
      const date = new Date(ch.created_at * 1000).toLocaleDateString();
      return `
        <div class="chp-item" id="chpi-${ch.id}">
          <div class="chp-item-body" onclick="loadHistoryChat(${ch.id})">
            <div class="chp-title">${escHtml(ch.title)}</div>
            <div class="chp-date">${date}</div>
          </div>
          <button class="btn btn-ghost btn-sm chp-del"
            onclick="deleteHistoryChat(${ch.id})" title="Delete">✕</button>
        </div>`;
    }).join('');
  } catch (e) {
    list.innerHTML = `<div class="placeholder-text" style="padding:16px 12px">${t('error_prefix')}${e.message}</div>`;
  }
}

async function loadHistoryChat(chatId) {
  try {
    const data = await (await fetch('/vyrii/history/chats/' + chatId)).json();
    if (data.error) { showToast(data.error); return; }
    state.chatMessages = data.messages || [];
    state.chatId       = chatId;
    state.savedCount   = state.chatMessages.length;
    renderChatMessages();
    updateCtxIndicator();
    document.getElementById('chat-hist-panel').classList.remove('open');
    const box = document.getElementById('chat-messages');
    if (box) box.scrollTop = box.scrollHeight;
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

async function deleteHistoryChat(chatId) {
  try {
    await fetch('/vyrii/history/chats/' + chatId, { method: 'DELETE' });
    document.getElementById('chpi-' + chatId)?.remove();
    const list = document.getElementById('chp-list');
    if (list && !list.querySelector('.chp-item')) {
      list.innerHTML = `<div class="placeholder-text" style="padding:16px 12px">${t('hist_empty')}</div>`;
    }
    if (state.chatId === chatId) newChat();
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

function renderChatMessages() {
  const container = document.getElementById('chat-messages');
  if (!state.chatMessages.length) {
    container.innerHTML = `<div class="placeholder-text" style="text-align:center;padding:40px 0">${t('chat_empty')}</div>`;
    return;
  }
  container.innerHTML = state.chatMessages.map((msg, i) => {
    const isUser = msg.role === 'user';
    const avatar  = isUser ? '👤' : '🤖';
    const cls     = isUser ? 'user' : 'asst';
    const name    = isUser ? 'You'  : 'Assistant';
    const content = md(msg.content);
    const cursor  = (!isUser && i === state.chatMessages.length - 1 && state.streaming)
      ? '<span class="cursor-blink"></span>' : '';
    return `
      <div class="msg-row ${cls}" id="msg-${i}">
        <div class="msg-avatar ${isUser ? 'usr' : 'bot'}">${avatar}</div>
        <div class="msg-wrap">
          <span class="msg-name">${name}</span>
          <div class="bubble">${content}${cursor}</div>
          <div class="msg-copy-group">
            <button class="msg-copy" onclick="copyMsgRaw(${i})" title="${t('copy_raw')}">MD</button>
            <button class="msg-copy" onclick="copyMsgFmt(${i})" title="${t('copy_fmt')}">&#128203;</button>${
              !isUser ? `<button class="msg-copy msg-retry" onclick="retryMsg(${i})" title="${t('retry_msg')}">&#x21bb;</button>` : ''}
          </div>
        </div>
      </div>`;
  }).join('');
  renderMermaid(container);
  container.scrollTop = container.scrollHeight;
}

function updateLastBubble() {
  const msgs = state.chatMessages;
  if (!msgs.length) return;
  const last = msgs[msgs.length - 1];
  const i    = msgs.length - 1;
  const el   = document.getElementById(`msg-${i}`);
  if (!el) { renderChatMessages(); return; }
  const bubble = el.querySelector('.bubble');
  if (!bubble) return;
  bubble.innerHTML = md(last.content) + (state.streaming ? '<span class="cursor-blink"></span>' : '');
  if (!state.streaming) renderMermaid(bubble);
  const container = document.getElementById('chat-messages');
  container.scrollTop = container.scrollHeight;
}

// ── HISTORY SAVE HELPERS ──────────────────────────────
async function _histEnsureChat(title) {
  if (state.chatId) return;
  try {
    const res = await fetch('/vyrii/history/chats', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title.slice(0, 50) }),
    });
    state.chatId = (await res.json()).id ?? null;
  } catch { /* offline or API-only mode without DB */ }
}

async function _histSaveMsg(role, content) {
  if (!state.chatId || !content) return;
  try {
    await fetch(`/vyrii/history/chats/${state.chatId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, content }),
    });
  } catch { /* best-effort */ }
}

function _setChatBusy(busy) {
  document.getElementById('chat-status').style.display = busy ? 'flex' : 'none';
  document.getElementById('chat-send').style.display   = busy ? 'none' : '';
  document.getElementById('chat-stop').style.display   = busy ? ''     : 'none';
}

function interruptChat() {
  if (state.abortCtrl) state.abortCtrl.abort();
}

async function sendChat() {
  if (state.streaming) return;
  const input = document.getElementById('chat-input');
  const text  = input.value.trim();
  if (!text) return;

  const model = getModel();
  if (!model) { showToast(t('no_model')); return; }

  input.value = '';
  input.style.height = 'auto';

  state.chatMessages.push({ role: 'user', content: text });
  state.chatMessages.push({ role: 'assistant', content: '' });
  state.streaming  = true;
  state.abortCtrl  = new AbortController();
  renderChatMessages();
  _setChatBusy(true);

  if (!state.incognito) {
    await _histEnsureChat(text);
    const saveUpTo = state.chatMessages.length - 1;
    for (let i = state.savedCount; i < saveUpTo; i++) {
      await _histSaveMsg(state.chatMessages[i].role, state.chatMessages[i].content);
    }
    state.savedCount = saveUpTo;
  }

  // messages to send: all except the last empty assistant placeholder
  const toSend = state.chatMessages.slice(0, -1);

  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, messages: toSend, stream: true,
        ...(state.smartCtx ? {} : { num_ctx: state.fixedCtx }) }),
      signal: state.abortCtrl.signal,
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (raw === '[DONE]') break;
        try {
          const obj   = JSON.parse(raw);
          if (obj.waiting) {
            state.chatMessages[state.chatMessages.length - 1].content =
              `⏳ ${t('queue_waiting')} (${obj.position})`;
            state._wasWaiting = true;
            updateLastBubble();
            continue;
          }
          const chunk = obj.choices?.[0]?.delta?.content ?? '';
          if (chunk) {
            if (state._wasWaiting) {
              state.chatMessages[state.chatMessages.length - 1].content = '';
              state._wasWaiting = false;
            }
            state.chatMessages[state.chatMessages.length - 1].content += chunk;
            updateLastBubble();
          }
        } catch { /* ignore malformed SSE line */ }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      state.chatMessages[state.chatMessages.length - 1].content = `${t('error_prefix')}${e.message}`;
    }
    // AbortError: keep whatever was generated so far
  } finally {
    state.streaming = false;
    state.abortCtrl = null;
    _setChatBusy(false);
    updateLastBubble();
    const last = state.chatMessages[state.chatMessages.length - 1];
    if (!state.incognito && last && last.role === 'assistant' && last.content) {
      await _histSaveMsg('assistant', last.content);
      state.savedCount = state.chatMessages.length;
    }
    updateCtxIndicator();
  }
}

// ── TRANSLATE ─────────────────────────────────────────
function swapLangs() {
  const from = document.getElementById('tr-from');
  const to   = document.getElementById('tr-to');
  [from.value, to.value] = [to.value, from.value];
}

async function runTranslate() {
  const text = document.getElementById('tr-input').value.trim();
  if (!text) return;
  setResultLoading('tr-result');
  try {
    const res = await fetch('/vyrii/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        from_lang: document.getElementById('tr-from').value,
        to_lang:   document.getElementById('tr-to').value,
        mode:      document.getElementById('tr-mode').value,
        model:     getModel(),
      }),
    });
    const data = await res.json();
    setResult('tr-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('tr-result', t('error_prefix') + e.message);
  }
}

// ── WEBASK ────────────────────────────────────────────
async function runWebAsk() {
  const question = document.getElementById('wa-question').value.trim();
  if (!question) return;
  setResultLoading('wa-result');
  try {
    const res = await fetch('/vyrii/webask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        url:   document.getElementById('wa-url').value.trim(),
        top_n: +document.getElementById('wa-n').value,
        model: getModel(),
      }),
    });
    const data = await res.json();
    setResultMd('wa-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('wa-result', t('error_prefix') + e.message);
  }
}

// ── WEBCRAWL ──────────────────────────────────────────
function wcUpdateVisibility() {
  const mode   = document.getElementById('wc-mode').value;
  const filter = document.getElementById('wc-filter').value;
  const needTask    = mode === 'llm' || filter === 'llm';
  const needFormat  = mode === 'llm';
  const needColumns = mode === 'extract' || mode === 'llm';
  document.getElementById('wc-task-wrap').style.display    = needTask    ? 'flex' : 'none';
  document.getElementById('wc-format-wrap').style.display  = needFormat  ? 'flex' : 'none';
  document.getElementById('wc-columns-wrap').style.display = needColumns ? 'flex' : 'none';
}

async function runWebCrawl() {
  const url = document.getElementById('wc-url').value.trim();
  if (!url) return;
  setResultLoading('wc-result');
  const format = document.querySelector('input[name="wc-format"]:checked')?.value || 'log';
  try {
    const res = await fetch('/vyrii/webcrawl', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        mode:       document.getElementById('wc-mode').value,
        filter:     document.getElementById('wc-filter').value,
        depth:      +document.getElementById('wc-depth').value,
        max_pages:  +document.getElementById('wc-pages').value,
        task:       document.getElementById('wc-task').value.trim(),
        format_out: format,
        ask:        document.getElementById('wc-ask').checked,
        columns:    document.getElementById('wc-columns').value.trim(),
        model:      getModel(),
      }),
    });
    const data = await res.json();
    setResultMd('wc-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('wc-result', t('error_prefix') + e.message);
  }
}

// ── WEBANALYS ─────────────────────────────────────────
async function runWebAnalys() {
  const query = document.getElementById('wan-query').value.trim();
  if (!query) return;
  setResultLoading('wan-result');
  try {
    const res = await fetch('/vyrii/webanalys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        n:     +document.getElementById('wan-n').value,
        model: getModel(),
      }),
    });
    const data = await res.json();
    setResultMd('wan-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('wan-result', t('error_prefix') + e.message);
  }
}

// ── DEEPAGENT ─────────────────────────────────────────
function daWebToggle() {
  const on = document.getElementById('da-web').checked;
  document.getElementById('da-web-n-wrap').style.display = on ? 'flex' : 'none';
}

function daRagToggle() {
  const on  = document.getElementById('da-rag-chk').checked;
  const wrap = document.getElementById('da-rag-wrap');
  wrap.style.display = on ? 'flex' : 'none';
  if (on) daRagRefresh();
}

async function daRagRefresh() {
  const sel = document.getElementById('da-rag-project');
  try {
    const res  = await fetch('/vyrii/rag/projects');
    const data = await res.json();
    const projects = data.projects || [];
    const cur = sel.value;
    sel.innerHTML = `<option value="">${t('rag_select_project')}</option>`
      + projects.map(p => `<option value="${p}"${p === cur ? ' selected' : ''}>${p}</option>`).join('');
  } catch { /* ignore */ }
}

async function runDeepAgent() {
  const task = document.getElementById('da-task').value.trim();
  if (!task) return;

  const useTeam    = document.getElementById('da-team-chk').checked;
  const teamProfile = useTeam ? document.getElementById('da-team-profile').value : '';

  if (useTeam && teamProfile) {
    setResultLoading('da-result');
    const resultEl = document.getElementById('da-result');
    try {
      const res = await fetch('/vyrii/team/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile_name: teamProfile,
          query:        task,
          aspects:      [],
          combine:      document.getElementById('da-team-combine').value,
          ctx_mode:     'none',
          model:        getModel(),
          num_ctx:      4096,
          timeout:      300,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await _readTeamSSE(res, 'da-result');
    } catch (e) {
      setResult('da-result', t('error_prefix') + e.message);
    }
    return;
  }

  setResultLoading('da-result');
  const useWeb = document.getElementById('da-web').checked;
  const useRag = document.getElementById('da-rag-chk').checked;
  try {
    const res = await fetch('/vyrii/deepagent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task,
        ref_url:     document.getElementById('da-ref').value.trim(),
        sections:    +document.getElementById('da-sections').value,
        model:       getModel(),
        use_web:     useWeb,
        web_n:       useWeb ? +document.getElementById('da-web-n').value : 3,
        rag_project: useRag ? document.getElementById('da-rag-project').value : '',
      }),
    });
    const data = await res.json();
    setResultMd('da-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('da-result', t('error_prefix') + e.message);
  }
}

// ── SCAN ──────────────────────────────────────────────
async function runScan() {
  const path = document.getElementById('sc-path').value.trim();
  if (!path) return;
  setResultLoading('sc-result');
  try {
    const res = await fetch('/vyrii/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path,
        query:   document.getElementById('sc-query').value.trim(),
        chunk:   +document.getElementById('sc-chunk').value,
        summary: +document.getElementById('sc-summary').value,
        target:  +document.getElementById('sc-target').value,
        rounds:  +document.getElementById('sc-rounds').value,
        ext:     document.getElementById('sc-ext').value.trim(),
        model:   getModel(),
      }),
    });
    const data = await res.json();
    setResult('sc-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('sc-result', t('error_prefix') + e.message);
  }
}

// ── WEBINDEX ──────────────────────────────────────────
async function runWebIndex() {
  const url = document.getElementById('wi-url').value.trim();
  if (!url) return;
  setResultLoading('wi-result');
  try {
    const res = await fetch('/vyrii/webindex', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        project: document.getElementById('wi-project').value.trim(),
        path:    document.getElementById('wi-path').value.trim(),
        depth:   +document.getElementById('wi-depth').value,
        pages:   +document.getElementById('wi-pages').value,
        model:   getModel(),
      }),
    });
    const data = await res.json();
    setResult('wi-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('wi-result', t('error_prefix') + e.message);
  }
}

// ── OBFUSCATE ─────────────────────────────────────────
async function runObfuscate() {
  const text     = document.getElementById('of-input').value.trim();
  const glossary = document.getElementById('of-glossary').value.trim();
  if (!text || !glossary) { showToast('Text and glossary name required'); return; }
  setResultLoading('of-result');
  try {
    const res = await fetch('/vyrii/obfuscate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text, glossary,
        force: document.getElementById('of-force').checked,
        model: getModel(),
      }),
    });
    const data = await res.json();
    setResult('of-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('of-result', t('error_prefix') + e.message);
  }
}

async function runDeobfuscate() {
  const text     = document.getElementById('dof-input').value.trim();
  const glossary = document.getElementById('dof-glossary').value.trim();
  if (!text || !glossary) { showToast('Text and glossary name required'); return; }
  setResultLoading('dof-result');
  try {
    const res = await fetch('/vyrii/deobfuscate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text, glossary,
        force: document.getElementById('dof-force').checked,
        model: getModel(),
      }),
    });
    const data = await res.json();
    setResult('dof-result', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('dof-result', t('error_prefix') + e.message);
  }
}

// ── FILES ─────────────────────────────────────────────
state.filesLoaded = false;
state.currentFilePath = '';

async function refreshFiles() {
  const tree = document.getElementById('files-tree');
  tree.innerHTML = `<div class="placeholder-text">${t('loading')}</div>`;
  try {
    const res  = await fetch('/vyrii/files/list');
    const data = await res.json();
    if (data.error) { tree.innerHTML = `<div class="placeholder-text">${data.error}</div>`; return; }
    tree.innerHTML = '';
    tree.appendChild(buildTree(data.tree || {}, ''));
  } catch (e) {
    tree.innerHTML = `<div class="placeholder-text">${t('error_prefix')}${e.message}</div>`;
  }
}

function buildTree(node, basePath) {
  const ul = document.createElement('ul');
  ul.className = 'tree-list';
  for (const [name, children] of Object.entries(node || {})) {
    const li   = document.createElement('li');
    const isDir = name.endsWith('/');
    const item  = document.createElement('div');
    item.className = 'tree-item';
    const path  = basePath + name;

    if (isDir) {
      item.innerHTML = `<span class="item-icon">📁</span><span class="item-name">${name.slice(0,-1)}/</span>`;
      const sub = buildTree(children, path);
      sub.style.display = 'none';
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        sub.style.display = sub.style.display === 'none' ? 'block' : 'none';
        item.querySelector('.item-icon').textContent = sub.style.display === 'none' ? '📁' : '📂';
        showFileInfo({ name: name.slice(0,-1), path, type: 'directory' });
      });
      li.appendChild(item);
      li.appendChild(sub);
    } else {
      item.innerHTML = `<span class="item-icon">${fileIcon(name)}</span><span class="item-name">${name}</span>`;
      item.addEventListener('click', () => {
        document.querySelectorAll('.tree-item').forEach(i => i.classList.remove('selected'));
        item.classList.add('selected');
        showFileInfo({ name, path, type: 'file' });
      });
      li.appendChild(item);
    }
    ul.appendChild(li);
  }
  return ul;
}

function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  const icons = { py:'🐍', js:'📜', ts:'📜', html:'🌐', css:'🎨', md:'📝',
    txt:'📄', json:'📋', yaml:'📋', yml:'📋', sh:'⚙️', bat:'⚙️',
    png:'🖼️', jpg:'🖼️', jpeg:'🖼️', gif:'🖼️', svg:'🖼️',
    pdf:'📑', zip:'🗜️', gz:'🗜️', tar:'🗜️' };
  return icons[ext] || '📄';
}

function showFileInfo(info) {
  const preview = document.getElementById('files-preview');
  state.currentFilePath = info.path;
  const isFile = info.type === 'file';
  const escapedPath = info.path.replace(/'/g, "\\'");

  const actionBtns = isFile
    ? `<button class="btn btn-ghost btn-sm" onclick="viewFile('${escapedPath}')" data-i18n="view">View</button>
       <button class="btn btn-ghost btn-sm" onclick="fileScan('${escapedPath}')" data-i18n="scan_btn">Scan</button>`
    : `<button class="btn btn-ghost btn-sm" onclick="fileIndex('${escapedPath}')" data-i18n="index_btn">Index</button>
       <button class="btn btn-ghost btn-sm" onclick="fileScan('${escapedPath}')" data-i18n="scan_btn">Scan</button>`;

  preview.innerHTML = `
    <div class="file-info-box">
      <h3>${escHtml(info.name)}</h3>
      <div class="fi-row"><span class="fi-label">Type</span><span>${info.type}</span></div>
      <div class="fi-row"><span class="fi-label">Path</span>
        <span style="word-break:break-all;font-size:12px">${escHtml(info.path)}</span></div>
    </div>
    <div class="form-actions">
      ${actionBtns}
      <button class="btn btn-danger btn-sm" onclick="deleteFile('${escapedPath}')" data-i18n="delete_btn">Delete</button>
    </div>
    <div id="file-op-result" style="display:none" class="result-box" style="flex:1"></div>
    <div id="file-content-wrap" style="display:none">
      <div style="display:flex;align-items:center;gap:8px;margin-top:8px">
        <label class="form-label" style="margin:0" id="file-content-label"></label>
        <div id="file-view-toggle" style="display:none;margin-left:auto;gap:4px;display:none">
          <button id="fv-raw"      class="btn btn-ghost btn-sm fv-btn active" onclick="_fileViewMode('raw')">Raw</button>
          <button id="fv-rendered" class="btn btn-ghost btn-sm fv-btn"        onclick="_fileViewMode('rendered')">Rendered</button>
        </div>
      </div>
      <div id="file-content-box" class="file-raw"></div>
      <div id="file-truncated-note" style="display:none;font-size:11px;color:var(--text-muted);margin-top:4px">
        ⚠ File truncated at 64 KB
      </div>
    </div>`;
  applyLang(state.lang);
}

async function viewFile(path) {
  const wrap   = document.getElementById('file-content-wrap');
  const box    = document.getElementById('file-content-box');
  const lbl    = document.getElementById('file-content-label');
  const note   = document.getElementById('file-truncated-note');
  const toggle = document.getElementById('file-view-toggle');
  wrap.style.display  = 'block';
  box.className       = 'file-raw';
  box.textContent     = 'Loading…';
  lbl.textContent     = path.split('/').pop() || path;
  toggle.style.display = 'none';
  try {
    const res  = await fetch('/vyrii/files/read?path=' + encodeURIComponent(path));
    const data = await res.json();
    if (data.error) { box.textContent = data.error; return; }
    const content = data.content || '';
    state.fileViewRaw  = content;
    note.style.display = data.truncated ? 'block' : 'none';
    const ext = path.split('.').pop().toLowerCase();
    const canRender = ['md', 'markdown', 'html', 'htm'].includes(ext);
    if (canRender) {
      toggle.style.display = 'flex';
      _fileViewMode('rendered');
    } else {
      toggle.style.display = 'none';
      _fileViewMode('raw');
    }
  } catch (e) {
    box.textContent = t('error_prefix') + e.message;
  }
}

const _HLJS_LANG = {
  py:'python', js:'javascript', ts:'typescript', jsx:'javascript', tsx:'typescript',
  java:'java', rs:'rust', go:'go', rb:'ruby', php:'php', cs:'csharp', kt:'kotlin',
  swift:'swift', cpp:'cpp', c:'c', h:'c', r:'r',
  sh:'bash', bash:'bash', zsh:'bash',
  css:'css', scss:'css', html:'html', htm:'html', xml:'xml',
  json:'json', yaml:'yaml', yml:'yaml', toml:'toml', sql:'sql', md:'markdown',
};

function _fileViewMode(mode) {
  const box = document.getElementById('file-content-box');
  if (!box) return;
  const raw  = state.fileViewRaw || '';
  const path = state.currentFilePath || '';
  const ext  = path.split('.').pop().toLowerCase();

  document.querySelectorAll('.fv-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('fv-' + mode)?.classList.add('active');

  if (mode === 'raw') {
    box.className  = 'file-raw';
    box.style.cssText = '';
    // syntax highlighting + line numbers via hljs
    if (typeof hljs !== 'undefined' && raw) {
      const lang = _HLJS_LANG[ext] || '';
      let highlighted;
      try {
        highlighted = (lang && hljs.getLanguage(lang))
          ? hljs.highlight(raw, { language: lang, ignoreIllegals: true }).value
          : hljs.highlightAuto(raw).value;
      } catch { highlighted = escHtml(raw); }
      const lines = highlighted.split('\n');
      const rows  = lines.map((line, i) =>
        `<span class="code-line"><span class="code-ln">${i + 1}</span><span>${line || ' '}</span></span>`
      ).join('\n');
      box.innerHTML = `<code class="hljs" style="display:table;width:100%">${rows}</code>`;
    } else {
      box.textContent = raw || '(empty)';
    }
  } else {
    if (ext === 'md' || ext === 'markdown') {
      box.className = 'result-box';
      box.style.maxHeight = '55vh';
      box.style.overflow  = 'auto';
      box.innerHTML = md(raw);
      renderMermaid(box);
    } else if (ext === 'html' || ext === 'htm') {
      box.className = '';
      box.style.cssText = 'width:100%;margin-top:6px';
      const iframe = document.createElement('iframe');
      iframe.setAttribute('sandbox', 'allow-forms');
      iframe.style.cssText = 'width:100%;height:55vh;border:1px solid var(--border);border-radius:8px;background:#fff;display:block';
      iframe.srcdoc = raw;
      box.innerHTML = '';
      box.appendChild(iframe);
    }
  }
}

async function fileIndex(path) {
  const result = document.getElementById('file-op-result');
  result.style.display = 'block';
  result.innerHTML = `<span class="status-bar"><span class="status-dot"></span>Indexing…</span>`;
  try {
    const res  = await fetch('/vyrii/files/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (data.ok) {
      result.textContent = `Index OK — project "${data.project}"`;
    } else {
      result.textContent = data.error || t('api_error');
    }
  } catch (e) {
    result.textContent = t('error_prefix') + e.message;
  }
}

async function fileScan(path) {
  const result = document.getElementById('file-op-result');
  result.style.display = 'block';
  result.innerHTML = `<span class="status-bar"><span class="status-dot"></span>Scanning…</span>`;
  try {
    const res  = await fetch('/vyrii/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path,
        query: '', chunk: 4000, summary: 400,
        target: 8000, rounds: 1, ext: '',
        model: getModel(),
      }),
    });
    const data = await res.json();
    result.textContent = data.result ?? data.error ?? t('api_error');
  } catch (e) {
    result.textContent = t('error_prefix') + e.message;
  }
}

async function deleteFile(path) {
  if (!confirm(`Delete: ${path}?`)) return;
  try {
    const res  = await fetch('/vyrii/files', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (data.ok) { showToast('Deleted'); refreshFiles(); }
    else showToast(data.error || t('api_error'));
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

function showMkdir() {
  document.getElementById('mkdir-dialog').style.display = 'flex';
  document.getElementById('mkdir-name').focus();
}
function closeMkdir() {
  document.getElementById('mkdir-dialog').style.display = 'none';
  document.getElementById('mkdir-name').value = '';
}

async function doMkdir() {
  const name = document.getElementById('mkdir-name').value.trim();
  if (!name) return;
  try {
    const res  = await fetch('/vyrii/files/mkdir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: name }),
    });
    const data = await res.json();
    if (data.ok) { showToast('Created'); closeMkdir(); refreshFiles(); }
    else showToast(data.error || t('api_error'));
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

// ── RAG ───────────────────────────────────────────────
let _ragContext = '';

async function ragRefreshProjects() {
  const sel = document.getElementById('rag-project');
  try {
    const res  = await fetch('/vyrii/rag/projects');
    const data = await res.json();
    const projects = data.projects || [];
    const current  = sel.value;
    sel.innerHTML = `<option value="">${t('rag_select_project')}</option>`
      + projects.map(p => `<option value="${p}"${p === current ? ' selected' : ''}>${p}</option>`).join('');
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

async function runRagSearch() {
  const project = document.getElementById('rag-project').value.trim();
  const query   = document.getElementById('rag-query').value.trim();
  const topk    = +document.getElementById('rag-topk').value;
  if (!project || !query) { showToast('Select a project and enter a query'); return; }

  setResultLoading('rag-results');
  document.getElementById('rag-ask-btn').style.display  = 'none';
  document.getElementById('rag-llm-col').style.display  = 'none';
  document.getElementById('rag-sources').style.display  = 'none';
  _ragContext = '';

  try {
    const res  = await fetch('/vyrii/rag/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project, query, top_k: topk }),
    });
    const data = await res.json();
    if (data.error) { setResult('rag-results', data.error); return; }

    _ragContext = data.context || '';
    const results = data.results || [];
    if (!results.length) { setResult('rag-results', 'No results found.'); return; }

    // render results
    const html = results.map(r => `
      <div style="margin-bottom:14px">
        <div style="font-weight:600;margin-bottom:4px">
          ${r.rank}. ${escHtml(r.file)}
          <span style="color:var(--text-muted);font-weight:400;font-size:12px"> score: ${r.score}</span>
        </div>
        <pre style="white-space:pre-wrap;font-size:12px;max-height:200px;overflow-y:auto">${escHtml(r.text)}</pre>
      </div>`).join('');
    document.getElementById('rag-results').innerHTML = html;

    // sources strip
    document.getElementById('rag-sources').style.display = 'block';
    document.getElementById('rag-sources-list').textContent = (data.sources || []).join('  ·  ');

    // show Ask LLM button
    document.getElementById('rag-ask-btn').style.display = 'inline-flex';
  } catch (e) {
    setResult('rag-results', t('error_prefix') + e.message);
  }
}

async function runRagAsk() {
  const query = document.getElementById('rag-query').value.trim();
  if (!_ragContext || !query) return;
  document.getElementById('rag-llm-col').style.display = 'flex';
  setResultLoading('rag-llm-out');
  try {
    const res  = await fetch('/vyrii/rag/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, context: _ragContext, model: getModel() }),
    });
    const data = await res.json();
    setResultMd('rag-llm-out', data.result ?? data.error ?? t('api_error'));
  } catch (e) {
    setResult('rag-llm-out', t('error_prefix') + e.message);
  }
}

// ── SETTINGS ──────────────────────────────────────────
async function loadSettings() {
  try {
    const res  = await fetch('/vyrii/settings');
    const cfg  = await res.json();
    const set  = (id, val) => { if (val !== undefined && val !== null) document.getElementById(id).value = val; };
    set('cfg-url',            cfg.saved_url || 'http://localhost:11434');
    set('cfg-backend',        cfg.saved_backend || 'ollama');
    set('cfg-timeout',        cfg.timeout || 180);
    set('cfg-worker-timeout', cfg.worker_timeout || 300);
    set('cfg-model',          cfg.saved_model || '');
    set('cfg-lang',           cfg.lang || 'en');
    set('cfg-auth-user',      cfg.auth_user || 'admin');
    await loadProfileOptions();
    set('cfg-active-profile', cfg.active_profile || '');
    const rmode = cfg.reserve_mode || 'response';
    const rEl = document.getElementById(rmode === 'timer' ? 'cfg-reserve-timer' : 'cfg-reserve-response');
    if (rEl) rEl.checked = true;
    set('cfg-reserve-timeout', cfg.reserve_timeout || 600);
  } catch { /* offline — keep defaults */ }
}

async function saveAuth() {
  const username = document.getElementById('cfg-auth-user').value.trim();
  const password = document.getElementById('cfg-auth-pass').value;
  if (!username) { showToast('Username required'); return; }
  if (!password) { showToast('Password required'); return; }
  try {
    const res = await fetch('/vyrii/auth/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('cfg-auth-pass').value = '';
      const s = document.getElementById('auth-status');
      s.style.display = 'inline';
      setTimeout(() => { s.style.display = 'none'; }, 1500);
      // reload so browser prompts for new credentials
      setTimeout(() => location.reload(), 1800);
    } else {
      showToast(data.error || t('api_error'));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

async function saveSettings() {
  const payload = {
    saved_url:      document.getElementById('cfg-url').value.trim()           || null,
    saved_backend:  document.getElementById('cfg-backend').value              || null,
    timeout:        +document.getElementById('cfg-timeout').value             || null,
    worker_timeout: +document.getElementById('cfg-worker-timeout').value      || null,
    saved_model:    document.getElementById('cfg-model').value.trim()         || null,
    lang:           document.getElementById('cfg-lang').value                 || null,
    active_profile: document.getElementById('cfg-active-profile').value       || '',
    reserve_mode:   document.querySelector('input[name="reserve-mode"]:checked')?.value || 'response',
    reserve_timeout: +document.getElementById('cfg-reserve-timeout').value   || 600,
  };
  try {
    const res  = await fetch('/vyrii/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      const status = document.getElementById('settings-status');
      status.style.display = 'inline';
      setTimeout(() => { status.style.display = 'none'; }, 2500);
      // apply lang change immediately if changed
      if (payload.lang) setLang(payload.lang);
      loadModels();
    } else {
      showToast(data.error || t('api_error'));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

// ── SYSTEM CONTROL ────────────────────────────────────
function _sysStatus(msg, isError) {
  const el = document.getElementById('sys-status');
  el.style.display = 'block';
  el.style.color = isError ? '#ef4444' : 'var(--accent)';
  el.textContent = msg;
}

async function sysRestart() {
  _sysStatus('Restarting…', false);
  try {
    await fetch('/vyrii/system/restart', { method: 'POST' });
    _sysStatus('Vyrii is restarting. Reloading page in 5 s…', false);
    setTimeout(() => location.reload(), 5000);
  } catch {
    _sysStatus('Restart signal sent. Reload the page manually.', false);
    setTimeout(() => location.reload(), 5000);
  }
}

async function sysReboot() {
  const confirmed = document.getElementById('sys-confirm').checked;
  if (!confirmed) { _sysStatus('Check the confirmation box first.', true); return; }
  try {
    const res  = await fetch('/vyrii/system/reboot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed }),
    });
    const data = await res.json();
    _sysStatus(data.message ?? data.error ?? 'Done.', !!data.error);
  } catch (e) {
    _sysStatus(t('error_prefix') + e.message, true);
  }
}

async function sysShutdown() {
  const confirmed = document.getElementById('sys-confirm').checked;
  if (!confirmed) { _sysStatus('Check the confirmation box first.', true); return; }
  try {
    const res  = await fetch('/vyrii/system/shutdown', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed }),
    });
    const data = await res.json();
    _sysStatus(data.message ?? data.error ?? 'Done.', !!data.error);
  } catch (e) {
    _sysStatus(t('error_prefix') + e.message, true);
  }
}

async function uploadFiles(input) {
  if (!input.files.length) return;
  const form = new FormData();
  for (const f of input.files) form.append('files', f);
  try {
    const res  = await fetch('/vyrii/files/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (data.ok) {
      showToast(`Uploaded: ${(data.saved || []).join(', ')}`);
      refreshFiles();
    } else {
      showToast(JSON.stringify(data.error || t('api_error')));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
  input.value = '';
}

// ── SSE HELPER (team/run) ─────────────────────────────
// Reads the SSE stream from /vyrii/team/run into a result box.
// Shows progress inline; writes final result when done.
async function _readTeamSSE(res, resultId, progressLogId) {
  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop() ?? '';
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      try {
        const item = JSON.parse(line.slice(6));
        if (item.type === 'progress') {
          if (progressLogId) {
            const log = document.getElementById(progressLogId);
            if (log) {
              const d = document.createElement('div');
              d.innerHTML = md(item.text);
              log.appendChild(d);
            }
          } else {
            const el = document.getElementById(resultId);
            if (el) el.innerHTML = `<span class="status-bar"><span class="status-dot"></span>${escHtml(item.text)}</span>`;
          }
        } else if (item.type === 'done') {
          setResultMd(resultId, item.text);
          if (progressLogId) {
            const wrap = document.getElementById(progressLogId)?.parentElement;
            if (wrap) wrap.style.display = 'none';
          }
        } else if (item.type === 'error') {
          setResult(resultId, 'Error: ' + item.text);
          if (progressLogId) {
            const wrap = document.getElementById(progressLogId)?.parentElement;
            if (wrap) wrap.style.display = 'none';
          }
        }
      } catch { /* ignore malformed SSE */ }
    }
  }
}

// ── DEEPAGENT TEAM HELPERS ────────────────────────────
function daTeamToggle() {
  const on = document.getElementById('da-team-chk').checked;
  document.getElementById('da-team-wrap').style.display = on ? 'flex' : 'none';
  if (on) daTeamRefresh();
}

async function daTeamRefresh() {
  const sel = document.getElementById('da-team-profile');
  try {
    const res  = await fetch('/vyrii/team/profiles');
    const data = await res.json();
    const cur  = sel.value;
    sel.innerHTML = '<option value="">— select profile —</option>'
      + (data.profiles || []).map(p =>
          `<option value="${escHtml(p.name)}"${p.name === cur ? ' selected' : ''}>${escHtml(p.name)}</option>`
        ).join('');
  } catch { /* ignore */ }
}

// ── PROFILE ───────────────────────────────────────────
let _profileList = [];

async function profileLoad() {
  try {
    const res  = await fetch('/vyrii/team/profiles');
    const data = await res.json();
    _profileList = data.profiles || [];
    _renderProfileList();
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

function _renderProfileList() {
  const el = document.getElementById('profile-list');
  if (!_profileList.length) {
    el.innerHTML = '<div class="placeholder-text" style="font-size:12px">No profiles</div>';
    return;
  }
  el.innerHTML = '';
  _profileList.forEach(p => {
    const btn = document.createElement('button');
    btn.className = 'btn btn-ghost btn-sm';
    btn.style.cssText = 'text-align:left;justify-content:flex-start;width:100%';
    btn.textContent = p.name;
    btn.addEventListener('click', () => profileSelect(p.name));
    el.appendChild(btn);
  });
}

function profileNew() {
  document.getElementById('prof-name').value    = '';
  document.getElementById('prof-comment').value = '';
  document.getElementById('prof-workers').innerHTML = '';
  profileAddWorker();
}

function profileSelect(name) {
  const p = _profileList.find(x => x.name === name);
  if (!p) return;
  document.getElementById('prof-name').value    = p.name    || '';
  document.getElementById('prof-comment').value = p.comment || '';
  const container = document.getElementById('prof-workers');
  container.innerHTML = '';
  (p.workers || []).forEach(w => profileAddWorker(w.host || '', w.model || '', w.provider || 'ollama'));
}

function profileAddWorker(host = '', model = '', provider = 'ollama') {
  const container = document.getElementById('prof-workers');
  const div = document.createElement('div');
  div.className = 'form-row worker-row';
  div.style.gap = '6px';
  div.innerHTML = `
    <input type="text" class="form-control" placeholder="localhost:11434"
           value="${escHtml(host)}" style="flex:2">
    <input type="text" class="form-control" placeholder="gemma3:1b"
           value="${escHtml(model)}" style="flex:2">
    <select class="form-control" style="flex:1">
      <option value="ollama"${provider === 'ollama' ? ' selected' : ''}>Ollama</option>
      <option value="openai"${provider === 'openai' ? ' selected' : ''}>OpenAI</option>
    </select>
    <button class="btn btn-danger btn-sm"
            onclick="this.closest('.worker-row').remove()" style="flex-shrink:0">✕</button>
  `;
  container.appendChild(div);
}

function _profileGetWorkers() {
  return Array.from(document.querySelectorAll('#prof-workers .worker-row')).map(row => {
    const inputs = row.querySelectorAll('input');
    const sel    = row.querySelector('select');
    return { host: inputs[0]?.value.trim() || '', model: inputs[1]?.value.trim() || '', provider: sel?.value || 'ollama' };
  }).filter(w => w.host && w.model);
}

async function profileSave() {
  const name = document.getElementById('prof-name').value.trim();
  if (!name) { showToast('Name is required'); return; }
  try {
    const res = await fetch('/vyrii/team/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        comment: document.getElementById('prof-comment').value.trim(),
        workers: _profileGetWorkers(),
      }),
    });
    const data = await res.json();
    if (data.ok) {
      const s = document.getElementById('profile-status');
      s.style.display = 'inline';
      setTimeout(() => { s.style.display = 'none'; }, 2000);
      profileLoad();
    } else {
      showToast(data.error || t('api_error'));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

async function profileDelete() {
  const name = document.getElementById('prof-name').value.trim();
  if (!name) return;
  if (!confirm(`Delete profile "${name}"?`)) return;
  try {
    const res  = await fetch(`/vyrii/team/profile/${encodeURIComponent(name)}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('prof-name').value    = '';
      document.getElementById('prof-comment').value = '';
      document.getElementById('prof-workers').innerHTML = '';
      profileLoad();
    } else {
      showToast(data.error || t('api_error'));
    }
  } catch (e) {
    showToast(t('error_prefix') + e.message);
  }
}

// ── TEAM ──────────────────────────────────────────────
async function teamLoadProfiles() {
  const sel = document.getElementById('team-profile');
  try {
    const res  = await fetch('/vyrii/team/profiles');
    const data = await res.json();
    const cur  = sel.value;
    sel.innerHTML = '<option value="">— select profile —</option>'
      + (data.profiles || []).map(p =>
          `<option value="${escHtml(p.name)}"${p.name === cur ? ' selected' : ''}>${escHtml(p.name)}</option>`
        ).join('');
  } catch { /* ignore */ }
}

async function teamLoadProfile() {
  const name      = document.getElementById('team-profile').value;
  const wrap      = document.getElementById('team-aspects-wrap');
  const container = document.getElementById('team-aspects');
  if (!name) { wrap.style.display = 'none'; return; }
  try {
    const res  = await fetch(`/vyrii/team/profile/${encodeURIComponent(name)}`);
    const prof = await res.json();
    const workers = prof.workers || [];
    container.innerHTML = '';
    workers.forEach(w => {
      const div = document.createElement('div');
      div.style.cssText = 'display:flex;align-items:center;gap:8px';
      div.innerHTML = `
        <span style="font-size:12px;color:var(--text-muted);width:180px;flex-shrink:0">
          ${escHtml(w.model || '')} @ ${escHtml(w.host || '')}
        </span>
        <input type="text" class="form-control aspect-input"
               placeholder="Aspect (optional)…" style="flex:1">
      `;
      container.appendChild(div);
    });
    wrap.style.display = workers.length ? 'block' : 'none';
  } catch { /* ignore */ }
}

async function runTeam() {
  const profile = document.getElementById('team-profile').value;
  const query   = document.getElementById('team-query').value.trim();
  if (!profile) { showToast('Select a profile'); return; }
  if (!query)   { showToast('Enter a query'); return; }

  const aspects      = Array.from(document.querySelectorAll('#team-aspects .aspect-input')).map(i => i.value.trim());
  const progressLog  = document.getElementById('team-progress-log');
  const progressWrap = document.getElementById('team-progress');
  progressLog.innerHTML = '';
  progressWrap.style.display = 'block';
  setResultLoading('team-result');

  try {
    const res = await fetch('/vyrii/team/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profile_name: profile,
        query,
        aspects,
        combine:  document.getElementById('team-combine').value,
        ctx_mode: document.getElementById('team-ctx').value,
        model:    getModel(),
        num_ctx:  4096,
        timeout:  300,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await _readTeamSSE(res, 'team-result', 'team-progress-log');
  } catch (e) {
    setResult('team-result', t('error_prefix') + e.message);
    progressWrap.style.display = 'none';
  }
}

// ── PROJECTS ──────────────────────────────────────────
async function projRefresh() {
  try {
    const data = await (await fetch('/vyrii/projects')).json();
    const list = document.getElementById('proj-list');
    if (!list) return;
    const projects = data.projects || [];
    if (!projects.length) {
      list.innerHTML = `<div style="font-size:13px;color:var(--text-muted)">${t('loading').replace('…','') + ' — none yet'}</div>`;
      return;
    }
    list.innerHTML = projects.map(p => `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:var(--input-bg);
                  border:1px solid var(--border);border-radius:6px">
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:13px">${esc(p.name)}</div>
          <div style="font-size:11px;color:var(--text-muted);word-break:break-all">${esc(p.path)}</div>
          ${p.description ? `<div style="font-size:11px;color:var(--text-muted)">${esc(p.description)}</div>` : ''}
        </div>
        <button class="btn btn-danger btn-sm" onclick="projDelete('${esc(p.name)}')"
                style="flex-shrink:0" data-i18n="proj_delete_confirm">✕</button>
      </div>`).join('');
  } catch (e) { /* ignore */ }
}

async function projAdd() {
  const name = document.getElementById('proj-name').value.trim();
  const path = document.getElementById('proj-path').value.trim();
  const desc = document.getElementById('proj-desc').value.trim();
  if (!name || !path) { showToast('Name and path are required'); return; }
  await fetch('/vyrii/projects', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name, path, description: desc }) });
  document.getElementById('proj-name').value = '';
  document.getElementById('proj-path').value = '';
  document.getElementById('proj-desc').value = '';
  projRefresh();
  loadProjectSelects();
}

async function projDelete(name) {
  await fetch(`/vyrii/projects/${encodeURIComponent(name)}`, { method: 'DELETE' });
  projRefresh();
  loadProjectSelects();
}

async function loadProjectSelects() {
  try {
    const data = await (await fetch('/vyrii/projects')).json();
    const projects = data.projects || [];
    const opts = `<option value="">— select project —</option>` +
      projects.map(p => `<option value="${esc(p.name)}">${esc(p.name)} — ${esc(p.path)}</option>`).join('');
    ['sim-project', 'svy-project'].forEach(id => {
      const el = document.getElementById(id);
      if (el) { const cur = el.value; el.innerHTML = opts; if (cur) el.value = cur; }
    });
  } catch { /* offline */ }
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ── GENERIC CLI RUN HELPER ────────────────────────────
async function _runCmd(command, cwd, resultId, busyId, showStderr = true) {
  if (busyId) { const b = document.getElementById(busyId); if (b) b.style.display = ''; }
  const box = document.getElementById(resultId);
  if (box) box.innerHTML = `<span style="color:var(--text-muted)">${t('loading')}</span>`;
  try {
    const data = await (await fetch('/vyrii/run', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ command, cwd: cwd || '' }),
    })).json();
    if (busyId) { const b = document.getElementById(busyId); if (b) b.style.display = 'none'; }
    if (data.error) { if (box) box.textContent = 'Error: ' + data.error; return; }
    const out = (data.stdout || '') + (showStderr && data.stderr ? '\n[stderr]\n' + data.stderr : '');
    const status = data.returncode === 0
      ? t('run_ok').replace('{code}', data.returncode).replace('{dur}', data.duration_s)
      : t('run_error').replace('{code}', data.returncode);
    if (box) box.textContent = status + '\n\n' + out.trim();
  } catch (e) {
    if (busyId) { const b = document.getElementById(busyId); if (b) b.style.display = 'none'; }
    if (box) box.textContent = t('error_prefix') + e.message;
  }
}

function _getProject(selectId, infoId) {
  const sel = document.getElementById(selectId);
  if (!sel || !sel.value) { showToast('Select a project first'); return null; }
  return sel.value;
}

function _getProjectPath(selectId) {
  const sel = document.getElementById(selectId);
  if (!sel || !sel.value) return null;
  const opt = sel.options[sel.selectedIndex];
  // path is encoded in the option text after ' — '
  const text = opt ? opt.textContent : '';
  const idx = text.indexOf(' — ');
  return idx >= 0 ? text.slice(idx + 3) : null;
}

// ── SIMARGL ───────────────────────────────────────────
function simSubtab(name) {
  ['index','search','rrf'].forEach(n => {
    document.getElementById(`sim-pane-${n}`).style.display = n === name ? '' : 'none';
    document.getElementById(`sim-tab-${n}`).classList.toggle('subtab-active', n === name);
  });
}

function simProjectChanged() {
  const path = _getProjectPath('sim-project');
  const info = document.getElementById('sim-path-info');
  if (info) info.textContent = path ? path : '';
}

async function simRunRrf() {
  const path = _getProjectPath('sim-project');
  if (!path) { showToast('Select a project first'); return; }
  const query   = document.getElementById('rrf-query').value.trim();
  if (!query) { showToast('Enter a task description'); return; }
  const sources = document.getElementById('rrf-sources').value.trim() || 'task:default,file:default';
  const topn    = document.getElementById('rrf-topn').value    || '10';
  const topk    = document.getElementById('rrf-topk').value    || '10';
  const k       = document.getElementById('rrf-k').value       || '60';
  const sort    = document.getElementById('rrf-sort').value    || 'freq';
  const format  = document.getElementById('rrf-format').value  || 'text';
  const blend   = document.getElementById('rrf-blend').value   || '1.0';
  const showStderr = document.getElementById('rrf-stderr').checked;

  let cmd = `simargl rrf ${JSON.stringify(query)}`
    + ` --sources ${sources} --store-dir .simargl`
    + ` --top-n ${topn} --top-k ${topk} --k ${k}`
    + ` --sort ${sort} --format ${format}`;
  if (parseFloat(blend) !== 1.0) cmd += ` --score-blend ${blend}`;

  await _runCmd(cmd, path, 'sim-result', null, showStderr);
}

async function simRunIndex() {
  const name = _getProject('sim-project', 'sim-path-info');
  if (!name) return;
  const path = _getProjectPath('sim-project');
  const cmd = `simargl index files . --project ${name} --store .simargl`;
  await _runCmd(cmd, path, 'sim-result', null);
}

function simModeChanged() {
  const mode = document.getElementById('sim-mode').value;
  const isTask   = mode === 'task';
  const needsTopK = mode !== 'file';
  const el = (id) => document.getElementById(id);
  if (el('sim-sort-wrap'))     el('sim-sort-wrap').style.display     = isTask ? '' : 'none';
  if (el('sim-topk-wrap'))     el('sim-topk-wrap').style.display     = needsTopK ? '' : 'none';
}

async function simRunSearch() {
  const name = _getProject('sim-project', 'sim-path-info');
  if (!name) return;
  const path = _getProjectPath('sim-project');
  const query = document.getElementById('sim-query').value.trim();
  if (!query) { showToast('Enter a task description'); return; }

  const mode    = document.getElementById('sim-mode').value    || 'file';
  const format  = document.getElementById('sim-format').value  || 'text';
  const topn    = document.getElementById('sim-topn').value    || '10';
  const topk    = document.getElementById('sim-topk').value    || '10';
  const sort    = document.getElementById('sim-sort').value    || 'rank';
  const diff       = document.getElementById('sim-diff').checked;
  const noBH       = document.getElementById('sim-noblackholes').checked;
  const showStderr = document.getElementById('sim-stderr').checked;

  let cmd = `simargl search ${JSON.stringify(query)}`
    + ` --project ${name} --store-dir .simargl`
    + ` --mode ${mode} --format ${format} --top-n ${topn}`;
  if (mode !== 'file') cmd += ` --top-k ${topk}`;
  if (mode === 'task') cmd += ` --sort ${sort}`;
  if (diff)  cmd += ' --diff';
  if (noBH)  cmd += ' --no-blackholes';

  await _runCmd(cmd, path, 'sim-result', null, showStderr);
}

function _homeDir() {
  // best-effort: resolve ~ based on known API paths (not needed server-side)
  return '';
}

// ── SVITOVYD ──────────────────────────────────────────
function svySubtab(name) {
  ['index','find','trace','deps','sym','kw','idiff'].forEach(n => {
    document.getElementById(`svy-pane-${n}`).style.display = n === name ? '' : 'none';
    document.getElementById(`svy-tab-${n}`).classList.toggle('subtab-active', n === name);
  });
}

function svyProjectChanged() {
  const path = _getProjectPath('svy-project');
  const info = document.getElementById('svy-path-info');
  if (info) info.textContent = path ? path : '';
}

async function svyRun(op) {
  const name = _getProject('svy-project', 'svy-path-info');
  if (!name) return;
  const path = _getProjectPath('svy-project');
  let cmd = '';

  if (op === 'index') {
    const depth = document.getElementById('svy-depth').value || '2';
    cmd = `svitovyd index . ${depth} --stdout`;
  } else if (op === 'find') {
    const q = document.getElementById('svy-find-q').value.trim();
    if (!q) { showToast('Enter query tokens'); return; }
    cmd = `svitovyd find ${q}`;
  } else if (op === 'trace') {
    const id = document.getElementById('svy-trace-id').value.trim();
    if (!id) { showToast('Enter identifier'); return; }
    const depth = document.getElementById('svy-trace-depth').value || '8';
    cmd = `svitovyd trace ${id} --depth ${depth}`;
  } else if (op === 'deps') {
    const id = document.getElementById('svy-deps-id').value.trim();
    if (!id) { showToast('Enter identifier'); return; }
    const depth = document.getElementById('svy-deps-depth').value || '8';
    cmd = `svitovyd deps ${id} --depth ${depth}`;
  } else if (op === 'sym') {
    const k = document.getElementById('svy-sym-k').value || '5';
    cmd = `svitovyd sym --k ${k}`;
  } else if (op === 'kw') {
    const taskText = document.getElementById('svy-kw-task').value.trim();
    const k = document.getElementById('svy-kw-k').value || '50';
    const fuzzy = document.getElementById('svy-kw-fuzzy').checked ? ' -f' : '';
    if (taskText) {
      cmd = `svitovyd keywords extract ${JSON.stringify(taskText)}${fuzzy}`;
    } else {
      cmd = `svitovyd keywords --k ${k}`;
    }
  } else if (op === 'idiff') {
    const prev = document.getElementById('svy-idiff-prev').value.trim();
    if (!prev) { showToast('Enter previous map file path'); return; }
    cmd = `svitovyd idiff --prev ${prev}`;
  }

  await _runCmd(cmd, path, 'svy-result', 'svy-running');
}

// ── SCHEDULER ─────────────────────────────────────────
async function _schFetch(url, opts) {
  const res = await fetch(url, opts);
  return res.json();
}

function schTypeChanged() {
  const stype = document.getElementById('sch-stype').value;
  const timeRow = document.getElementById('sch-time-row');
  const dowWrap = document.getElementById('sch-dow-wrap');
  const intWrap = document.getElementById('sch-interval-wrap');
  const isInterval = stype.startsWith('interval_');
  if (timeRow) timeRow.style.display = isInterval ? 'none' : '';
  if (dowWrap) dowWrap.style.display = stype === 'weekly' ? '' : 'none';
  if (intWrap) intWrap.style.display = isInterval ? '' : 'none';
}

async function schRefresh() {
  try {
    const data = await _schFetch('/vyrii/scheduler/tasks');
    const tasks = data.tasks || [];
    const box = document.getElementById('sch-table');
    if (!box) return;
    if (!tasks.length) { box.textContent = 'No scheduled tasks yet.'; return; }
    const rows = tasks.map((task, i) => {
      const stype = task.schedule_type || 'daily';
      const h = String(task.hour || 9).padStart(2,'0');
      const m = String(task.minute || 0).padStart(2,'0');
      let sched = stype === 'daily' ? `Daily ${h}:${m}`
        : stype === 'weekly' ? `Weekly ${task.day_of_week || 'mon'} ${h}:${m}`
        : stype === 'monthly' ? `Monthly day ${task.interval_value||1} ${h}:${m}`
        : `Every ${task.interval_value||'?'} ${stype.split('_')[1]}`;
      const status = task.last_status || '—';
      const on = task.enabled !== false ? '✅' : '⏸';
      return `<div style="display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid var(--border)">
        <span style="font-size:11px;font-family:monospace;color:var(--text-muted);width:70px">${task.id.slice(0,8)}</span>
        <span style="flex:1;font-size:13px">${esc(task.name)}</span>
        <span style="font-size:11px;color:var(--text-muted);width:140px">${sched}</span>
        <span style="font-size:11px;width:60px">${status}</span>
        <span style="width:20px">${on}</span>
      </div>`;
    }).join('');
    box.innerHTML = rows;
  } catch (e) {
    const box = document.getElementById('sch-table');
    if (box) box.textContent = t('error_prefix') + e.message;
  }
}

async function schCreate() {
  const name    = document.getElementById('sch-name').value.trim();
  const command = document.getElementById('sch-command').value.trim();
  const stype   = document.getElementById('sch-stype').value;
  if (!name || !command) { showToast('Name and command required'); return; }
  const timeVal = document.getElementById('sch-time').value || '09:00';
  const [hh, mm] = timeVal.split(':').map(Number);
  const body = {
    name, command, schedule_type: stype,
    hour: hh || 9, minute: mm || 0,
    day_of_week: document.getElementById('sch-dow').value || 'mon',
    interval_value: parseInt(document.getElementById('sch-interval').value || '60'),
  };
  const data = await _schFetch('/vyrii/scheduler/tasks', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (data.error) { showToast(data.error); return; }
  showToast('Task created');
  document.getElementById('sch-name').value = '';
  document.getElementById('sch-command').value = '';
  schRefresh();
}

function _schId() {
  const v = (document.getElementById('sch-task-id').value || '').trim();
  if (!v) { showToast('Enter task ID prefix'); return null; }
  return v;
}

async function schToggle() {
  const prefix = _schId(); if (!prefix) return;
  const tasks = (await _schFetch('/vyrii/scheduler/tasks')).tasks || [];
  const task = tasks.find(t => t.id.startsWith(prefix));
  if (!task) { showToast('Task not found'); return; }
  const data = await _schFetch(`/vyrii/scheduler/tasks/${task.id}/toggle`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
  showToast(data.enabled ? 'Enabled' : 'Disabled');
  schRefresh();
}

async function schRunNow() {
  const prefix = _schId(); if (!prefix) return;
  const tasks = (await _schFetch('/vyrii/scheduler/tasks')).tasks || [];
  const task = tasks.find(t => t.id.startsWith(prefix));
  if (!task) { showToast('Task not found'); return; }
  await _schFetch(`/vyrii/scheduler/tasks/${task.id}/run`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
  showToast('Running in background');
}

async function schDelete() {
  const prefix = _schId(); if (!prefix) return;
  const tasks = (await _schFetch('/vyrii/scheduler/tasks')).tasks || [];
  const task = tasks.find(t => t.id.startsWith(prefix));
  if (!task) { showToast('Task not found'); return; }
  await _schFetch(`/vyrii/scheduler/tasks/${task.id}`, { method: 'DELETE' });
  showToast('Deleted');
  document.getElementById('sch-task-id').value = '';
  schRefresh();
}

async function schLoadLogs() {
  const prefix = (document.getElementById('sch-log-id').value || '').trim();
  if (!prefix) { showToast('Enter task ID prefix'); return; }
  const tasks = (await _schFetch('/vyrii/scheduler/tasks')).tasks || [];
  const task = tasks.find(t => t.id.startsWith(prefix));
  if (!task) { showToast('Task not found'); return; }
  const data = await _schFetch(`/vyrii/scheduler/tasks/${task.id}/logs`);
  const sel = document.getElementById('sch-log-sel');
  if (!sel) return;
  sel.innerHTML = (data.logs || []).map(l =>
    `<option value="${esc(l.filename)}">${esc(l.filename)}</option>`
  ).join('');
  if (sel.options.length) schReadLog(sel.options[0].value);
}

async function schReadLog(filename) {
  if (!filename) return;
  const box = document.getElementById('sch-log-content');
  if (box) box.textContent = t('loading');
  try {
    const data = await _schFetch(`/vyrii/scheduler/log?filename=${encodeURIComponent(filename)}`);
    if (box) box.textContent = data.content || '(empty)';
  } catch (e) {
    if (box) box.textContent = t('error_prefix') + e.message;
  }
}

// ── PROMPT LIBRARY ────────────────────────────────────
let _prmAll = [];

async function prmRefresh() {
  try {
    const data = await (await fetch('/vyrii/prompts')).json();
    _prmAll = data.prompts || [];
    prmRender(document.getElementById('prm-filter')?.value || '');
  } catch { /* offline */ }
}

function prmRender(filter) {
  const list = document.getElementById('prm-list');
  if (!list) return;
  const q = (filter || '').toLowerCase();
  const items = q
    ? _prmAll.filter(p =>
        (p.name||'').toLowerCase().includes(q) ||
        (p.description||'').toLowerCase().includes(q) ||
        (p.model||'').toLowerCase().includes(q) ||
        (p.area||'').toLowerCase().includes(q) ||
        (p.prompt||'').toLowerCase().includes(q)
      )
    : _prmAll;
  if (!items.length) {
    list.innerHTML = `<div style="font-size:13px;color:var(--text-muted)">${t('prm_none')}</div>`;
    return;
  }
  list.innerHTML = items.map(p => `
    <div style="padding:10px 12px;background:var(--input-bg);border:1px solid var(--border);border-radius:8px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">
        <span style="font-weight:600;font-size:13px;flex:1;min-width:80px">${esc(p.name)}</span>
        ${p.model ? `<span style="font-size:11px;padding:2px 8px;background:var(--accent-dim);color:var(--accent);border-radius:10px">${esc(p.model)}</span>` : ''}
        ${p.area  ? `<span style="font-size:11px;padding:2px 8px;background:var(--surface);color:var(--text-muted);border-radius:10px;border:1px solid var(--border)">${esc(p.area)}</span>` : ''}
      </div>
      ${p.description ? `<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">${esc(p.description)}</div>` : ''}
      <div style="font-size:12px;font-family:monospace;background:var(--code-bg);border-radius:4px;padding:8px;white-space:pre-wrap;word-break:break-word;max-height:140px;overflow-y:auto">${esc(p.prompt)}</div>
      <div style="display:flex;gap:6px;margin-top:8px;align-items:center">
        <button class="btn btn-primary btn-sm" onclick="prmAddToChat('${esc(p.id)}')" data-i18n="add_to_chat">Add to chat</button>
        <button class="btn btn-ghost btn-sm" onclick="prmCopy('${esc(p.id)}')" data-i18n="copy">Copy</button>
        <button class="btn btn-danger btn-sm" onclick="prmDelete('${esc(p.id)}')" style="margin-left:auto">✕</button>
      </div>
    </div>`).join('');
}

function prmFilter() {
  prmRender(document.getElementById('prm-filter')?.value || '');
}

async function prmAdd() {
  const name   = document.getElementById('prm-name').value.trim();
  const prompt = document.getElementById('prm-prompt').value.trim();
  if (!name || !prompt) { showToast('Name and prompt text are required'); return; }
  await fetch('/vyrii/prompts', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      name,
      prompt,
      description: document.getElementById('prm-desc').value.trim(),
      model:       document.getElementById('prm-model').value.trim(),
      area:        document.getElementById('prm-area').value.trim(),
    }),
  });
  ['prm-name','prm-desc','prm-model','prm-area','prm-prompt'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  prmRefresh();
}

async function prmDelete(id) {
  await fetch(`/vyrii/prompts/${encodeURIComponent(id)}`, { method: 'DELETE' });
  prmRefresh();
}

function prmAddToChat(id) {
  const p = _prmAll.find(x => x.id === id);
  if (!p) return;
  const inp = document.getElementById('chat-input');
  if (!inp) return;
  inp.value = inp.value ? inp.value + '\n\n' + p.prompt : p.prompt;
  autoResize(inp);
  switchTab('chat');
  inp.focus();
}

function prmCopy(id) {
  const p = _prmAll.find(x => x.id === id);
  if (!p) return;
  navigator.clipboard.writeText(p.prompt).then(() => showToast(t('copied')));
}
