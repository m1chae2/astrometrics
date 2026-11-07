/**
 * @fileoverview FileInput: the one file picker of the app. It is a native file input. The app
 * usually hides it and opens it from a Button, so keep the `ref`.
 */

import React from 'react';

export type FileInputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'>;

/**
 * A file picker input.
 * @param props See {@link FileInputProps}. All props go to the native input.
 * @return The input element.
 */
export const FileInput = React.forwardRef<HTMLInputElement, FileInputProps>((props, ref) => (
    <input {...props} ref={ref} type="file" />
));
FileInput.displayName = 'FileInput';
