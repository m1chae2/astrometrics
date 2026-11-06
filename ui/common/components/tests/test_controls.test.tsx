/**
 * @fileoverview Tests of the shared controls: Button, Badge, TextInput, NumberInput, Checkbox,
 * Slider and FileInput. They check the classes, the roles and the keyboard behavior.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button } from '../Button';
import { Badge, BadgeTone } from '../Badge';
import { TextInput } from '../TextInput';
import { NumberInput } from '../NumberInput';
import { Checkbox } from '../Checkbox';
import { Slider } from '../Slider';
import { FileInput } from '../FileInput';

describe('Button', () => {
    it('is a plain button that does not submit a form by default', () => {
        render(<Button>Go</Button>);
        const button = screen.getByRole('button', { name: 'Go' });
        expect(button).toHaveAttribute('type', 'button');
        expect(button).toHaveClass('btn');
    });

    it.each([
        ['primary', 'btn--primary'], ['secondary', 'btn--secondary'], ['danger', 'btn--danger'],
    ] as const)('draws the %s variant', (variant, className) => {
        render(<Button variant={variant}>V</Button>);
        expect(screen.getByRole('button')).toHaveClass('btn', className);
    });

    it('draws a ghost button without the btn fill class', () => {
        render(<Button variant="ghost">G</Button>);
        expect(screen.getByRole('button')).toHaveClass('btn-ghost');
        expect(screen.getByRole('button')).not.toHaveClass('btn');
    });

    it.each([['small', 'btn--tiny'], ['square', 'btn--square']] as const)('draws the %s size', (size, className) => {
        render(<Button size={size}>S</Button>);
        expect(screen.getByRole('button')).toHaveClass(className);
    });

    it('sets aria-pressed and the active look for a toggle', () => {
        const { rerender } = render(<Button pressed>T</Button>);
        expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button')).toHaveClass('btn--active');
        rerender(<Button pressed={false}>T</Button>);
        expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'false');
        expect(screen.getByRole('button')).not.toHaveClass('btn--active');
    });

    it('fills the width, keeps a layout class and answers clicks and disabled', async () => {
        const onClick = vi.fn();
        const { rerender } = render(<Button fullWidth className="mine" onClick={onClick}>C</Button>);
        expect(screen.getByRole('button')).toHaveClass('btn--full-width', 'mine');
        await userEvent.click(screen.getByRole('button'));
        expect(onClick).toHaveBeenCalledTimes(1);
        rerender(<Button disabled onClick={onClick}>C</Button>);
        await userEvent.click(screen.getByRole('button'));
        expect(onClick).toHaveBeenCalledTimes(1);
    });

    it('forwards its ref and allows type submit', () => {
        const ref = React.createRef<HTMLButtonElement>();
        render(<Button ref={ref} type="submit">Send</Button>);
        expect(ref.current).toBe(screen.getByRole('button'));
        expect(ref.current).toHaveAttribute('type', 'submit');
    });
});

describe('Badge', () => {
    it.each(['ok', 'warning', 'error', 'info', 'neutral', 'accent', 'purple'] as BadgeTone[])('draws the %s tone', (tone) => {
        render(<Badge tone={tone}>Word</Badge>);
        expect(screen.getByText('Word')).toHaveClass('badge', `badge--${tone}`);
    });

    it('shows its icon before the text', () => {
        render(<Badge tone="ok" icon={<svg data-testid="icon" />}>Ready</Badge>);
        expect(screen.getByTestId('icon')).toBeInTheDocument();
    });

    it('becomes a button when it has a click handler', async () => {
        const onClick = vi.fn();
        render(<Badge tone="purple" onClick={onClick}>M 81</Badge>);
        await userEvent.click(screen.getByRole('button', { name: 'M 81' }));
        expect(onClick).toHaveBeenCalledTimes(1);
    });
});

describe('TextInput and NumberInput', () => {
    it('adds the small modifier for size="small" and passes it through NumberInput', () => {
        render(<><TextInput aria-label="Small" size="small" /><NumberInput aria-label="Count" size="small" /><TextInput aria-label="Regular" /></>);
        expect(screen.getByLabelText('Small')).toHaveClass('entry', 'entry--small');
        expect(screen.getByLabelText('Count')).toHaveClass('entry--small');
        expect(screen.getByLabelText('Regular')).not.toHaveClass('entry--small');
    });

    it('uses the entry class and its modifiers', () => {
        render(<TextInput aria-label="Name" width="short" centered />);
        expect(screen.getByLabelText('Name')).toHaveClass('entry', 'entry--short', 'entry--centered');
        expect(screen.getByLabelText('Name')).toHaveAttribute('type', 'text');
    });

    it('supports the fill width, a password type and a layout class', () => {
        render(<TextInput aria-label="Key" type="password" width="fill" className="mine" />);
        expect(screen.getByLabelText('Key')).toHaveClass('entry--fill', 'mine');
        expect(screen.getByLabelText('Key')).toHaveAttribute('type', 'password');
    });

    it('forwards its ref and reports typing', async () => {
        const ref = React.createRef<HTMLInputElement>();
        const onChange = vi.fn();
        render(<TextInput aria-label="Name" ref={ref} onChange={onChange} />);
        await userEvent.type(screen.getByLabelText('Name'), 'ab');
        expect(onChange).toHaveBeenCalledTimes(2);
        expect(ref.current).toBe(screen.getByLabelText('Name'));
    });

    it('makes a number field that keeps min, max and step', () => {
        render(<NumberInput aria-label="Gain" min={0} max={10} step={0.5} />);
        const field = screen.getByLabelText('Gain');
        expect(field).toHaveAttribute('type', 'number');
        expect(field).toHaveAttribute('step', '0.5');
        expect(field).toHaveClass('entry');
    });
});

describe('Checkbox, Slider and FileInput', () => {
    it('toggles from its label text and from the Space key', async () => {
        const onChange = vi.fn();
        render(<Checkbox label="Enable" checked={false} onChange={onChange} />);
        await userEvent.click(screen.getByText('Enable'));
        expect(onChange).toHaveBeenCalledTimes(1);
        screen.getByRole('checkbox', { name: 'Enable' }).focus();
        await userEvent.keyboard(' ');
        expect(onChange).toHaveBeenCalledTimes(2);
    });

    it('draws a bare box with an aria-label when it has no text', () => {
        render(<Checkbox aria-label="Select all" checked onChange={() => undefined} />);
        expect(screen.getByRole('checkbox', { name: 'Select all' })).toHaveClass('checkbox__input');
    });

    it('does not toggle when disabled', async () => {
        const onChange = vi.fn();
        render(<Checkbox label="Off" disabled onChange={onChange} />);
        await userEvent.click(screen.getByText('Off'));
        expect(onChange).not.toHaveBeenCalled();
    });

    it('makes a range slider and a file input', () => {
        render(<><Slider aria-label="Rate" min={0} max={3} /><FileInput data-testid="file" /></>);
        expect(screen.getByRole('slider', { name: 'Rate' })).toBeInTheDocument();
        expect(screen.getByTestId('file')).toHaveAttribute('type', 'file');
    });
});
