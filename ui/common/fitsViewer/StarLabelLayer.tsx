/**
 * @file StarLabelLayer.tsx
 * @description The name labels of the FITS viewer's star overlay: a reticle on each labeled star
 * and a badge with its name. A badge opens the star in the Astronomy Manager when the star has a
 * library record. The hovered and selected stars are drawn last, so they sit on top.
 */
import React from 'react';
import type { AstrometryOverlayStar } from '../services/astronomyService';
import { canOpenInAstronomyManager } from './utils/starNames';
import { computeBadgePlacement, estimateBadgeWidth, formatStarDisplayName, isStarSelected } from './utils/starOverlayLayout';

/** Props for StarLabelLayer. */
interface Props {
  /** The overlay stars, placed on the picture as it is shown. */
  stars: AstrometryOverlayStar[];
  /** The ids of the stars that get a label. */
  labeledIds: Set<string>;
  /** The selected star's id. */
  selectedStarId: string | null | undefined;
  /** The star under the pointer. */
  hoveredStarId: string | null;
  /** Sets the star under the pointer. */
  onHoverChange: (id: string | null) => void;
  /** Opens a star in the Astronomy Manager. */
  onStarClick?: (star: AstrometryOverlayStar) => void;
  /** Screen pixels per overlay pixel, across. */
  scaleX: number;
  /** Screen pixels per overlay pixel, down. */
  scaleY: number;
  /** The picture's offset on screen. */
  panX: number;
  /** The picture's offset on screen. */
  panY: number;
  /** The viewer's width on screen, so a badge near the right edge flips to the left. */
  containerW: number;
}

/**
 * Draws the labeled stars' reticles and name badges as one SVG layer.
 *
 * @param props See `Props`.
 * @returns The SVG layer.
 */
export const StarLabelLayer: React.FC<Props> = ({
  stars, labeledIds, selectedStarId, hoveredStarId, onHoverChange, onStarClick, scaleX, scaleY, panX, panY, containerW,
}) => (
  <svg
    className="fits-renderer__overlay"
    data-overlay-layer="true"
    style={{
      position: 'absolute',
      top: 0,
      left: 0,
      width: '100%',
      height: '100%',
      pointerEvents: 'none',
      overflow: 'hidden'
    }}
  >
    {/* Render unselected/unhovered stars first, hovered and selected stars last so they render on top */}
    {stars
      .filter((star) => labeledIds.has(star.id))
      .sort((a, b) => {
        const aSelected = isStarSelected(a, selectedStarId);
        const bSelected = isStarSelected(b, selectedStarId);
        const aHovered = a.id === hoveredStarId;
        const bHovered = b.id === hoveredStarId;
        const aScore = (aSelected ? 2 : 0) + (aHovered ? 1 : 0);
        const bScore = (bSelected ? 2 : 0) + (bHovered ? 1 : 0);
        return aScore - bScore;
      })
      .map((star) => {
        const screenX = panX + star.x * scaleX;
        const screenY = panY + star.y * scaleY;
        const displayName = formatStarDisplayName(star.name);
        const badgeWidth = estimateBadgeWidth(star);
        const badgeHeight = 24;
        const badgeX = computeBadgePlacement(screenX, badgeWidth, containerW);
        const badgeY = -12;
        const isHovered = hoveredStarId === star.id;
        const isSelected = isStarSelected(star, selectedStarId);
        // Size the reticle to the detected star (falls back to the old fixed size).
        const measuredRadius = star.radiusPx ? star.radiusPx * Math.min(scaleX, scaleY) : 0;
        const ringRadius = measuredRadius > 0 ? Math.min(Math.max(measuredRadius + 4, 8), 60) : 15;
        const tickInner = ringRadius + 1;
        const tickOuter = ringRadius + 6;

        return (
          <g
            key={star.id}
            className={`fits-renderer__star-group ${isHovered ? 'fits-renderer__star-group--hovered' : ''} ${isSelected ? 'fits-renderer__star-group--selected' : ''}`}
            transform={`translate(${screenX}, ${screenY})`}
            onPointerEnter={() => onHoverChange(star.id)}
            onPointerLeave={() => onHoverChange(null)}
            data-star-id={star.id}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                // Space must not scroll the page while it opens the star.
                event.preventDefault();
                if (canOpenInAstronomyManager(star)) onStarClick?.(star);
              }
            }}
            role="button"
            tabIndex={0}
          >
            <title>{onStarClick && canOpenInAstronomyManager(star) ? `Open ${star.name} in the Astronomy Manager` : star.name}</title>

            {/* Precision Reticle: Ring */}
            <circle
              cx={0}
              cy={0}
              r={ringRadius}
              className="fits-renderer__reticle-ring"
            />
            {/* Precision Reticle: Cardinal Ticks */}
            <line x1={0} y1={-tickOuter} x2={0} y2={-tickInner} className="fits-renderer__reticle-ticks" />
            <line x1={0} y1={tickInner} x2={0} y2={tickOuter} className="fits-renderer__reticle-ticks" />
            <line x1={-tickOuter} y1={0} x2={-tickInner} y2={0} className="fits-renderer__reticle-ticks" />
            <line x1={tickInner} y1={0} x2={tickOuter} y2={0} className="fits-renderer__reticle-ticks" />
            {/* Precision Reticle: Center Point */}
            <circle cx={0} cy={0} r={1.5} className="fits-renderer__reticle-center" />

            {/* Pill Badge Surface */}
            <rect
              x={badgeX}
              y={badgeY}
              width={badgeWidth}
              height={badgeHeight}
              rx={5}
              ry={5}
              className="fits-renderer__badge-bg"
            />

            {/* Star Display Name */}
            <text
              x={badgeX + 8}
              y={0}
              dominantBaseline="central"
              className="fits-renderer__badge-name"
            >
              {displayName}
            </text>

            {/* Navigation Arrow */}
            <text
              x={badgeX + badgeWidth - 14}
              y={0}
              dominantBaseline="central"
              className="fits-renderer__badge-action"
            >
              →
            </text>
          </g>
        );
      })}
  </svg>
);
