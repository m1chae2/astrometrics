/**
 * @fileoverview CustomSelect Component
 * A dark-themed, accessible custom select dropdown matching the GNOME Adwaita /
 * Observatory design system with native-styled overlay scrollbars and keyboard navigation.
 */
import React, { useState, useRef, useEffect, useId } from 'react';
import './customSelect.css';

export interface CustomSelectProps {
  options: string[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
  placeholder?: string;
}

/**
 * CustomSelect provides an accessible, fully stylable replacement for HTML <select>
 * ensuring dark-mode styling, consistent border radius, and matching Adwaita scrollbars.
 */
export const CustomSelect: React.FC<CustomSelectProps> = ({
  options,
  value,
  onChange,
  className = '',
  placeholder = 'Select option...',
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const selectId = useId();

  // Close dropdown on outside click
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  // Scroll active item into view when opening
  useEffect(() => {
    if (isOpen && listRef.current) {
      const activeEl = listRef.current.querySelector('.custom-select__option--selected');
      if (activeEl) {
        (activeEl as HTMLElement).scrollIntoView({ block: 'nearest' });
      }
    }
  }, [isOpen]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setIsOpen((prev) => !prev);
    } else if (e.key === 'Escape') {
      setIsOpen(false);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!isOpen) {
        setIsOpen(true);
      } else {
        const currentIndex = options.indexOf(value);
        if (currentIndex < options.length - 1) {
          onChange(options[currentIndex + 1]);
        }
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (!isOpen) {
        setIsOpen(true);
      } else {
        const currentIndex = options.indexOf(value);
        if (currentIndex > 0) {
          onChange(options[currentIndex - 1]);
        }
      }
    }
  };

  const handleSelect = (option: string) => {
    onChange(option);
    setIsOpen(false);
  };

  const selectedLabel = value || placeholder;

  return (
    <div
      ref={containerRef}
      className={`custom-select ${isOpen ? 'custom-select--open' : ''} ${className}`}
      onKeyDown={handleKeyDown}
    >
      <button
        type="button"
        id={selectId}
        className="custom-select__trigger"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((prev) => !prev)}
      >
        <span className="custom-select__label">{selectedLabel}</span>
        <span className="custom-select__arrow" aria-hidden="true" />
      </button>

      {isOpen && (
        <ul
          ref={listRef}
          className="custom-select__menu"
          role="listbox"
          aria-labelledby={selectId}
          tabIndex={-1}
        >
          {options.map((option) => {
            const isSelected = option === value;
            return (
              <li
                key={option}
                role="option"
                aria-selected={isSelected}
                className={`custom-select__option ${isSelected ? 'custom-select__option--selected' : ''}`}
                onClick={() => handleSelect(option)}
              >
                {option}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};
