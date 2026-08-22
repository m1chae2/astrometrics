import { useState, useEffect, useRef } from 'react';

export const useSettingsModal = () => {
    const [open, setOpen] = useState(false);
    const [closing, setClosing] = useState(false);
    const prevBodyOverflow = useRef<string | null>(null);

    const handleOpen = (): void => {
        setClosing(false);
        setOpen(true);
    };

    const handleClose = (): void => {
        setClosing(true);
    };

    // Animation
    useEffect(() => {
        if (closing) {
            const t = window.setTimeout(() => {
                setOpen(false);
                setClosing(false);
            }, 240);
            return () => {
                window.clearTimeout(t);
            };
        }
        return undefined;
    }, [closing]);

    // Escape Key
    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent): void => {
            if (e.key === 'Escape') {
                handleClose();
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open]);

    // Body Scroll
    useEffect(() => {
        if (open || closing) {
            prevBodyOverflow.current = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
        } else {
            if (prevBodyOverflow.current !== null) {
                document.body.style.overflow = prevBodyOverflow.current;
            }
            prevBodyOverflow.current = null;
        }
        return () => {
            if (prevBodyOverflow.current !== null) {
                document.body.style.overflow = prevBodyOverflow.current;
            }
        };
    }, [open, closing]);

    return {
        open,
        closing,
        handleOpen,
        handleClose
    };
};
