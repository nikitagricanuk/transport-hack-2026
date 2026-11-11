import { useEffect, useRef, useState } from 'react';
import { TOOLS } from './Panels';
const chapters = [
  [
    'Открыть файл',
    'Загрузите .osm или откройте исходный образец. Граф намеренно содержит ошибки: исправленный файл не подменяет работу редактора.',
  ],
  [
    'Слои',
    'Каждый слой независим. Остановки показаны синими квадратами. Узлы, светофоры и переходы можно скрыть отдельно. Порядок отрисовки меняется стрелками.',
  ],
  [
    'Инструменты и горячие клавиши',
    'Ctrl/Cmd+клик добавляет объект в выбор; Shift+перетаскивание выделяет рамкой. Alt+два клика по ребру выбирают участок. Esc сбрасывает выбор. Ctrl+Z отменяет одну транзакцию.',
  ],
  [
    'Ошибки и исправления',
    'Приложение находит ошибки и предлагает исправления. Выберите один вариант, проверьте предпросмотр на карте и только затем нажмите «Применить». Анализ и предпросмотр не меняют файл. Причина сохраняется в журнале.',
  ],
  [
    'Светофоры и остановки',
    'T ставит светофор на перекрёстке или переходе; на трамвайной линии — трамвайный. Длительности зелёного вводятся в секундах. План хранится в uds:signal:*. SUMO строит свои программы.',
  ],
  [
    'Перекрытия',
    'В свойствах дороги нажмите «Перекрыть». Сценарий переводит поток на объезд. Отсутствие объезда в выгрузке не означает отсутствие пути в городе.',
  ],
  [
    'Аналитика',
    'Маршрут строится по времени или длине. Изохроны учитывают частично пройденные рёбра. Доступность ОП сравнивает наборы остановок A и B. Ширина коридора, скорости и модель спроса — явные допущения; население и рельеф не рассчитаны.',
  ],
  [
    'Экспорт и проверки',
    'Экспорт сохраняет .osm и теги пользователя. Отчёты доступны в меню «Отчёты». Проверка организаторов отправляет файл наружу: не используйте её для разработки.',
  ],
];
export default function EditorMenus({
  onReport,
  onDownload,
}: {
  onReport: () => void;
  onDownload: (kind: 'docx' | 'xlsx' | 'pdf') => void;
}) {
  const [menu, setMenu] = useState<string | null>(null),
    [help, setHelp] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMenu(null);
        setHelp(false);
      }
    };
    const outside = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setMenu(null);
    };
    window.addEventListener('keydown', key);
    window.addEventListener('mousedown', outside);
    return () => {
      window.removeEventListener('keydown', key);
      window.removeEventListener('mousedown', outside);
    };
  }, []);
  return (
    <div className="row" ref={ref}>
      <div className="menu">
        <button
          className="menu-title"
          aria-expanded={menu === 'reports'}
          onClick={() => setMenu(menu === 'reports' ? null : 'reports')}
        >
          Отчёты
        </button>
        {menu === 'reports' && (
          <div role="menu" className="menu-list">
            <button
              role="menuitem"
              className="menu-item"
              onClick={() => {
                setMenu(null);
                onReport();
              }}
            >
              Отчёт с графиками…
            </button>
            {(['docx', 'xlsx', 'pdf'] as const).map((kind) => (
              <button
                role="menuitem"
                className="menu-item"
                key={kind}
                onClick={() => {
                  setMenu(null);
                  onDownload(kind);
                }}
              >
                Скачать {kind === 'docx' ? 'Word' : kind === 'xlsx' ? 'Excel' : 'PDF'}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="menu">
        <button
          className="menu-title"
          aria-expanded={menu === 'help'}
          onClick={() => setMenu(menu === 'help' ? null : 'help')}
        >
          Справка
        </button>
        {menu === 'help' && (
          <div role="menu" className="menu-list">
            <button
              role="menuitem"
              className="menu-item"
              onClick={() => {
                setMenu(null);
                setHelp(true);
              }}
            >
              Руководство пользователя
            </button>
            <a role="menuitem" className="menu-item" href="/mcp/connect" target="_blank" rel="noreferrer" onClick={() => setMenu(null)}>Подключить ИИ через MCP</a>
          </div>
        )}
      </div>
      {help && (
        <div className="modal-back" onClick={() => setHelp(false)}>
          <div
            className="modal"
            role="dialog"
            aria-label="Руководство пользователя"
            onClick={(e) => e.stopPropagation()}
            style={{ maxHeight: '85vh', overflow: 'auto', maxWidth: 900 }}
          >
            <button className="btn" onClick={() => setHelp(false)}>
              Закрыть
            </button>
            <h2>Руководство пользователя</h2>
            <nav>
              {chapters.map(([name], i) => (
                <p key={name}>
                  <a
                    href={`#help-${i}`}
                    onClick={(e) => {
                      e.preventDefault();
                      document.getElementById(`help-${i}`)?.scrollIntoView();
                    }}
                  >
                    {name}
                  </a>
                </p>
              ))}
            </nav>
            {chapters.map(([name, text], i) => (
              <section key={name} id={`help-${i}`}>
                <h3>{name}</h3>
                <p>{text}</p>
                {i === 2 && (
                  <ul>
                    {TOOLS.map((t) => (
                      <li key={t.id}>
                        <kbd>{t.key}</kbd> — {t.title}
                      </li>
                    ))}
                  </ul>
                )}
                {i === 1 && (
                  <img
                    src="/help/editor.png"
                    alt="Карта и независимые слои редактора"
                    style={{ maxWidth: '100%' }}
                  />
                )}
                {i === 6 && (
                  <img
                    src="/help/analysis.png"
                    alt="Панель маршрута и изохрон"
                    style={{ maxWidth: '100%' }}
                  />
                )}
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
