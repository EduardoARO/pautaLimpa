// @ts-nocheck

import AnalysisCarousel from '../components/analysis-carousel';
import logoSrc from './logo.png';

const BACKEND_URL = (process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || '').replace(/\/$/, '');

type Analysis = {
  key: string;
  texto: string;
  has_more?: boolean;
};

type DashboardItem = {
  id: number;
  title: string;
  ementa: string;
  data_apresentacao: string;
  link_oficial?: string | null;
  url_inteiro_teor?: string | null;
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

const FETCH_TIMEOUT_MS = Number(process.env.BACKEND_FETCH_TIMEOUT_MS || '60000');
const FETCH_MAX_RETRIES = Number(process.env.BACKEND_FETCH_RETRIES || '4');

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchDashboard(searchParams: Record<string, string | string[] | undefined>) {
  if (!BACKEND_URL) {
    throw new Error('BACKEND_URL não configurada no serviço do frontend.');
  }

  const params = new URLSearchParams();
  const dateFrom = typeof searchParams.date_from === 'string' ? searchParams.date_from : '';
  const dateTo = typeof searchParams.date_to === 'string' ? searchParams.date_to : '';
  const theme = typeof searchParams.theme === 'string' ? searchParams.theme : '';

  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo) params.set('date_to', dateTo);
  if (theme) params.set('theme', theme);

  const url = `${BACKEND_URL}/api/dashboard?${params.toString()}`;
  let lastError: Error | null = null;

  for (let attempt = 1; attempt <= FETCH_MAX_RETRIES; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

    try {
      const response = await fetch(url, {
        cache: 'no-store',
        signal: controller.signal,
      });

      const responseText = await response.text();

      if (response.ok) {
        return JSON.parse(responseText) as DashboardResponse;
      }

      // 502/503/504 costumam ser cold start do backend free; vale a pena tentar de novo.
      lastError = new Error(
        `Falha ao carregar o dashboard (${response.status} ${response.statusText}) via ${url}. Resposta: ${responseText.slice(0, 500)}`,
      );

      if (![502, 503, 504].includes(response.status)) {
        throw lastError;
      }
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
    } finally {
      clearTimeout(timeout);
    }

    if (attempt < FETCH_MAX_RETRIES) {
      await sleep(Math.min(2000 * attempt, 8000));
    }
  }

  throw lastError ?? new Error('Falha desconhecida ao carregar o dashboard.');
}

export default async function Page({ searchParams }: { searchParams: Record<string, string | string[] | undefined> }) {
  const data = await fetchDashboard(searchParams);
  const sortedGroups = sortGroupsByDayProximity(data.groups);

  return (
    <main className="shell">
      <header className="hero">
        <div className="brand">
          <div className="logo-slot">
            <img src={logoSrc.src} alt="PautaLimpa" width="64" height="64" />
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

                      <AnalysisCarousel apiBaseUrl={BACKEND_URL} projectId={item.id} analyses={item.analysis_order} />
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
