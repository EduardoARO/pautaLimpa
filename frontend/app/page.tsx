// @ts-nocheck

import AnalysisCarousel from '../components/analysis-carousel';

const BACKEND_URL =
  (typeof globalThis !== 'undefined' && globalThis.process?.env?.BACKEND_URL) ||
  'http://127.0.0.1:5000';

type Analysis = {
  key: string;
  texto: string;
};

type DashboardItem = {
  id: number;
  title: string;
  ementa: string;
  data_apresentacao: string;
  link_oficial?: string | null;
  url_inteiro_teor?: string | null;
  analyses: Record<string, { texto: string }>;
  analysis_order: Analysis[];
};

type DashboardResponse = {
  groups: Record<string, DashboardItem[]>;
  stats: Record<string, number>;
  date_from: string;
  date_to: string;
  theme: string;
  total_visible: number;
};

function parseDateLabel(dateLabel: string) {
  if (dateLabel === 'Sem data') return null;

  const [dayPart, monthPart, yearPart] = dateLabel.split('/');
  const day = Number(dayPart);
  const month = Number(monthPart);
  const year = Number(yearPart);

  if (!day || !month || !year) return null;

  const parsed = new Date(year, month - 1, day);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function getTodayDate() {
  const today = new Date();
  return new Date(today.getFullYear(), today.getMonth(), today.getDate());
}

function sortGroupsByDayProximity(groups: Record<string, DashboardItem[]>) {
  const todayDate = getTodayDate();

  return Object.entries(groups).sort(([leftLabel], [rightLabel]) => {
    const leftDate = parseDateLabel(leftLabel);
    const rightDate = parseDateLabel(rightLabel);

    if (!leftDate && !rightDate) return 0;
    if (!leftDate) return 1;
    if (!rightDate) return -1;

    const leftDistance = Math.abs(leftDate.getTime() - todayDate.getTime());
    const rightDistance = Math.abs(rightDate.getTime() - todayDate.getTime());

    if (leftDistance !== rightDistance) {
      return leftDistance - rightDistance;
    }

    return rightDate.getTime() - leftDate.getTime();
  });
}

async function fetchDashboard(searchParams: Record<string, string | string[] | undefined>) {
  const params = new URLSearchParams();
  const dateFrom = typeof searchParams.date_from === 'string' ? searchParams.date_from : '';
  const dateTo = typeof searchParams.date_to === 'string' ? searchParams.date_to : '';
  const theme = typeof searchParams.theme === 'string' ? searchParams.theme : '';

  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo) params.set('date_to', dateTo);
  if (theme) params.set('theme', theme);

  const response = await fetch(`${BACKEND_URL}/api/dashboard?${params.toString()}`, {
    cache: 'no-store',
  });

  if (!response.ok) {
    throw new Error('Falha ao carregar o dashboard');
  }

  return (await response.json()) as DashboardResponse;
}

export default async function Page({ searchParams }: { searchParams: Record<string, string | string[] | undefined> }) {
  const data = await fetchDashboard(searchParams);
  const sortedGroups = sortGroupsByDayProximity(data.groups);

  return (
    <main className="shell">
      <header className="hero">
        <div className="brand">
          <div className="logo-slot">
            <span>PL</span>
          </div>
          <div>
            <p className="eyebrow">PautaLimpa</p>
            <h2>A realidade transparente para você.</h2>
          </div>
        </div>
      </header>

      <section className="toolbar">
        <form method="get" className="filters">
          <label>
            De
            <input type="date" name="date_from" defaultValue={data.date_from} />
          </label>
          <label>
            Até
            <input type="date" name="date_to" defaultValue={data.date_to} />
          </label>
          <label>
            Tema
            <input type="search" name="theme" placeholder="Ex: saúde, etc" defaultValue={data.theme} />
          </label>
          <button type="submit">Atualizar</button>
        </form>
      </section>

      {Object.keys(data.groups).length === 0 ? (
        <section className="empty">
          <h2>Nenhum item encontrado</h2>
        </section>
      ) : (
        sortedGroups.map(([dateLabel, items]) => (
          <details key={dateLabel} className="date-group" open>
            <summary className="date-heading">
              <h2>{dateLabel}</h2>
              <span>{items.length} item(ns)</span>
            </summary>

            <div className="grid">
              {items.map((item) => (
                <article key={item.id} className="card">
                  <div className="card-top">
                    <div>
                      <h3>{item.title}</h3>
                    </div>
                  </div>

                  <div className="columns">
                    <section className="panel source">
                      <p className="panel-label">Ementa original da Câmara</p>
                      <p>{item.ementa}</p>
                    </section>

                    <section className="panel caption">
                      <div className="carousel-intro carousel-intro--compact">
                        <p className="panel-label">Análises de IA</p>
                        <p className="carousel-hint">
                          Arraste para ver as três leituras: esquerda, imparcial e direita.
                        </p>
                      </div>

                      <AnalysisCarousel analyses={item.analysis_order} />
                    </section>
                  </div>

                  <footer className="card-footer">
                    <span>Data de apresentação: {item.data_apresentacao}</span>
                    <div className="links">
                      {item.link_oficial ? (
                        <a href={item.link_oficial} target="_blank" rel="noreferrer">
                          Câmara
                        </a>
                      ) : null}
                      {item.url_inteiro_teor ? (
                        <a href={item.url_inteiro_teor} target="_blank" rel="noreferrer">
                          Inteiro teor
                        </a>
                      ) : null}
                    </div>
                  </footer>
                </article>
              ))}
            </div>
          </details>
        ))
      )}
    </main>
  );
}
