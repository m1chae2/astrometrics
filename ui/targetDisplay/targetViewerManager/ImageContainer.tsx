import React from 'react';
import './imageViewer/imageViewer.css';

/** Props for the ImageContainer component. */
interface Props {
  /** Source URL of the image. */
  src: string | null | undefined;
  /** Alt text for the image. */
  alt?: string;
  /** Optional custom styles for the container. */
  style?: React.CSSProperties;
  /** Optional CSS class names for the image element. */
  className?: string;
}

/**
 * Simple container wrapper around an <img> tag for consistent viewer layout.
 */
export const ImageContainer: React.FC<Props> = ({
  src,
  alt = '',
  style = {},
  ...props
}) => (
  <div className="image-viewer">
    {src ? (
      <img src={src} alt={alt} style={style} {...props} />
    ) : null}
  </div>
);
