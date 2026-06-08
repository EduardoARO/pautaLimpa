// @ts-nocheck

"use client";

import { useEffect, useMemo, useRef, useState } from 'react';
import useEmblaCarousel from 'embla-carousel-react';

type Analysis = {
  key: string;
  texto: string;
};

type Props = {
  analyses: Analysis[];
};

export default function AnalysisCarousel({ analyses }: Props) {
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
  const [overflowMap, setOverflowMap] = useState<Record<string, boolean>>({});
  const [activeAnalysis, setActiveAnalysis] = useState<Analysis | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const [emblaRef, emblaApi] = useEmblaCarousel({
    align: 'center',
    axis: 'x',
    containScroll: 'trimSnaps',
    dragFree: false,
    duration: 26,
    loop: false,
    skipSnaps: false,
    startIndex,
  });

  useEffect(() => {
    if (!emblaApi) return;
    emblaApi.reInit({
      align: 'center',
      axis: 'x',
      containScroll: 'trimSnaps',
      dragFree: false,
      duration: 26,
      loop: false,
      skipSnaps: false,
      startIndex,
    });
    emblaApi.scrollTo(startIndex, true);
  }, [emblaApi, startIndex]);

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
    requestAnimationFrame(() => setIsModalOpen(true));
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    closeTimerRef.current = window.setTimeout(() => {
      setActiveAnalysis(null);
    }, 220);
  };

  return (
    <>
      <div className="analysis-carousel" aria-label="Carrossel de análises ideológicas">
        <div className="analysis-carousel__viewport" ref={emblaRef}>
          <div className="analysis-carousel__container">
            {orderedAnalyses.map((analysis) => {
              const hasOverflow = Boolean(overflowMap[analysis.key]);

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

                  {hasOverflow ? (
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
              <pre>{activeAnalysis.texto}</pre>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
