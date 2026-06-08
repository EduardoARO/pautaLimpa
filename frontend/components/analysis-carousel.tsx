// @ts-nocheck

"use client";

import { useEffect, useMemo, useRef, useState } from 'react';
import useEmblaCarousel from 'embla-carousel-react';

type Analysis = {
  key: string;
  texto: string;
  has_more?: boolean;
};

type Props = {
  apiBaseUrl: string;
  projectId: number;
  analyses: Analysis[];
};

export default function AnalysisCarousel({ apiBaseUrl, projectId, analyses }: Props) {
  const orderedAnalyses = useMemo(() => {
    const priority: Record<string, number> = {
      ESQUERDA: 0,
      IMPARCIAL: 1,
      DIREITA: 2,
    };

    return [...analyses].sort((left, right) => {
      const leftPriority = priority[left.key] ?? 99;
      const rightPriority = priority[right.key] ?? 99;
      return leftPriority - rightPriority;
    });
  }, [analyses]);

  const startIndex = useMemo(
    () => Math.max(0, orderedAnalyses.findIndex((analysis) => analysis.key === 'IMPARCIAL')),
    [orderedAnalyses],
  );

  const slideBodyRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const closeTimerRef = useRef<number | null>(null);
  const requestIdRef = useRef(0);
  const [overflowMap, setOverflowMap] = useState<Record<string, boolean>>({});
  const [activeAnalysis, setActiveAnalysis] = useState<Analysis | null>(null);
  const [modalText, setModalText] = useState('');
  const [isLoadingFullText, setIsLoadingFullText] = useState(false);
  const [modalError, setModalError] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isMobileViewport, setIsMobileViewport] = useState(false);

  const carouselOptions = useMemo(
    () => ({
      align: 'center' as const,
      axis: 'x' as const,
      containScroll: 'trimSnaps' as const,
      dragFree: false,
      duration: 26,
      loop: false,
      skipSnaps: false,
      startIndex,
      breakpoints: {
        '(max-width: 640px)': {
          align: 'start' as const,
          containScroll: 'trimSnaps' as const,
          dragFree: false,
          duration: 22,
          skipSnaps: false,
        },
      },
    }),
    [startIndex],
  );

  const [emblaRef, emblaApi] = useEmblaCarousel(carouselOptions);

  useEffect(() => {
    if (!emblaApi) return;
    emblaApi.reInit(carouselOptions);
    emblaApi.scrollTo(startIndex, true);
  }, [carouselOptions, emblaApi, startIndex]);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const mediaQuery = window.matchMedia('(max-width: 640px)');
    const updateViewport = () => setIsMobileViewport(mediaQuery.matches);

    updateViewport();
    mediaQuery.addEventListener('change', updateViewport);

    return () => {
      mediaQuery.removeEventListener('change', updateViewport);
    };
  }, []);

  useEffect(() => {
    const measureOverflow = () => {
      const nextOverflowMap: Record<string, boolean> = {};

      for (const analysis of orderedAnalyses) {
        const element = slideBodyRefs.current[analysis.key];
        nextOverflowMap[analysis.key] = Boolean(element && element.scrollHeight > element.clientHeight + 6);
      }

      setOverflowMap(nextOverflowMap);
    };

    measureOverflow();

    const resizeObserver =
      typeof window !== 'undefined' && 'ResizeObserver' in window
        ? new ResizeObserver(() => measureOverflow())
        : null;

    if (resizeObserver) {
      for (const analysis of orderedAnalyses) {
        const element = slideBodyRefs.current[analysis.key];
        if (element) resizeObserver.observe(element);
      }
    }

    window.addEventListener('resize', measureOverflow);

    return () => {
      window.removeEventListener('resize', measureOverflow);
      resizeObserver?.disconnect();
    };
  }, [orderedAnalyses]);

  useEffect(() => {
    if (!activeAnalysis || !isModalOpen) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleCloseModal();
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [activeAnalysis, isModalOpen]);

  useEffect(() => {
    return () => {
      if (closeTimerRef.current) {
        window.clearTimeout(closeTimerRef.current);
      }
    };
  }, []);

  const handleOpenModal = (analysis: Analysis) => {
    if (closeTimerRef.current) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }

    setActiveAnalysis(analysis);
    setModalText(analysis.texto);
    setModalError('');
    setIsLoadingFullText(Boolean(analysis.has_more));
    requestAnimationFrame(() => setIsModalOpen(true));

    if (!analysis.has_more || !apiBaseUrl) {
      setIsLoadingFullText(false);
      return;
    }

    const currentRequestId = requestIdRef.current + 1;
    requestIdRef.current = currentRequestId;

    fetch(
      `${apiBaseUrl}/api/analysis-text?project_id=${encodeURIComponent(projectId)}&tipo_analise=${encodeURIComponent(
        analysis.key,
      )}`,
      { cache: 'no-store' },
    )
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload?.message || 'Não foi possível carregar o texto completo.');
        }
        return payload.texto as string;
      })
      .then((textoCompleto) => {
        if (requestIdRef.current !== currentRequestId) return;
        setModalText(textoCompleto);
        setIsLoadingFullText(false);
      })
      .catch((error: Error) => {
        if (requestIdRef.current !== currentRequestId) return;
        setModalError(error.message || 'Falha ao carregar o texto completo.');
        setIsLoadingFullText(false);
      });
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    closeTimerRef.current = window.setTimeout(() => {
      setActiveAnalysis(null);
      setModalText('');
      setModalError('');
      setIsLoadingFullText(false);
    }, 220);
  };

  return (
    <>
      <div className="analysis-carousel" aria-label="Carrossel de análises ideológicas">
        <div className="analysis-carousel__viewport" ref={emblaRef}>
          <div className="analysis-carousel__container">
            {orderedAnalyses.map((analysis) => {
              const hasOverflow = Boolean(overflowMap[analysis.key]);
              const shouldShowMoreButton = Boolean(analysis.has_more || hasOverflow || isMobileViewport);

              return (
                <article
                  key={analysis.key}
                  className={`analysis-slide analysis-card-${analysis.key.toLowerCase()}`}
                  data-analysis-key={analysis.key}
                >
                  <div className="analysis-slide-head">
                    <h4>{analysis.key.charAt(0) + analysis.key.slice(1).toLowerCase()}</h4>
                  </div>

                  <div
                    className="analysis-slide-body"
                    ref={(element) => {
                      slideBodyRefs.current[analysis.key] = element;
                    }}
                  >
                    <pre>{analysis.texto}</pre>
                  </div>

                  {shouldShowMoreButton || !isMobileViewport ? (
                    <button type="button" className="analysis-slide__more" onClick={() => handleOpenModal(analysis)}>
                      Mostrar mais
                    </button>
                  ) : null}
                </article>
              );
            })}
          </div>
        </div>
      </div>

      {activeAnalysis ? (
        <div
          className={`analysis-modal ${isModalOpen ? 'is-open' : ''}`}
          aria-hidden={!isModalOpen}
          onClick={handleCloseModal}
        >
          <div
            className={`analysis-modal__panel ${isModalOpen ? 'is-open' : ''}`}
            role="dialog"
            aria-modal="true"
            aria-label={`Texto completo da análise ${activeAnalysis.key.toLowerCase()}`}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="analysis-modal__head">
              <div>
                <p className="analysis-modal__eyebrow">Texto completo</p>
                <h4>{activeAnalysis.key.charAt(0) + activeAnalysis.key.slice(1).toLowerCase()}</h4>
              </div>
              <button type="button" className="analysis-modal__close" onClick={handleCloseModal}>
                ×
              </button>
            </div>

            <div className="analysis-modal__body">
              {isLoadingFullText ? <p className="analysis-modal__status">Carregando texto completo…</p> : null}
              {modalError ? <p className="analysis-modal__status analysis-modal__status--error">{modalError}</p> : null}
              <pre>{modalText}</pre>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
