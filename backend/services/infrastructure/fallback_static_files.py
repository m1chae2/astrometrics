"""A static file server that can look in a second folder.

Raw frames live in the frames folder and the pipeline's output (stacks,
previews, processed pictures) can live in another folder, set by the
``stacks_path`` setting. Both use the same layout, ``lights/<target>/...``,
so the image viewer asks for one URL either way. This server tries the frames
folder first and then the stacks folder.
"""

import os

from fastapi.staticfiles import StaticFiles


class FallbackStaticFiles(StaticFiles):
    """Serve files from one folder, falling back to a second folder.

    Parameters
    ----------
    directory : `str`
        The folder to look in first.
    fallback_directory : `str` or `None`, optional
        The folder to look in when `directory` has no such file. `None`
        serves from `directory` alone.
    """

    def __init__(self, *, directory: str, fallback_directory: str | None = None, **kwargs: object) -> None:
        """Set up the primary folder and, if given, the fallback one."""
        super().__init__(directory=directory, **kwargs)
        self._fallback = StaticFiles(directory=fallback_directory, **kwargs) if fallback_directory else None

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        """Find a file in the primary folder, then in the fallback folder.

        Parameters
        ----------
        path : `str`
            The requested path, relative to the mounted folder.

        Returns
        -------
        full_path : `str`
            The file's path on disk.
        stat_result : `os.stat_result` or `None`
            The file's details, or `None` when neither folder has it.
        """
        full_path, stat_result = super().lookup_path(path)
        if stat_result is None and self._fallback is not None:
            return self._fallback.lookup_path(path)
        return full_path, stat_result
