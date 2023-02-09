from __future__ import annotations

from typing import Any, Tuple, Union, Callable, Optional, Sequence, TYPE_CHECKING
from functools import wraps

from numba import njit, prange
from loguru import logger
from anndata import AnnData
from scipy.sparse import issparse, spmatrix
from scipy.spatial import KDTree
from pandas.api.types import infer_dtype, is_categorical_dtype
from matplotlib.colors import to_rgb, is_color_like
from scanpy.plotting._utils import add_colors_for_categorical_sample_annotation
from pandas.core.dtypes.common import (
    is_bool_dtype,
    is_object_dtype,
    is_string_dtype,
    is_integer_dtype,
    is_numeric_dtype,
)
import numpy as np
import pandas as pd
from pathlib import Path

from napari_spatialdata._constants._pkg_constants import Key

try:
    from numpy.typing import NDArray

    NDArrayA = NDArray[Any]
except (ImportError, TypeError):
    NDArray = np.ndarray  # type: ignore[misc]
    NDArrayA = np.ndarray  # type: ignore[misc]


Vector_name_t = Tuple[Optional[Union[pd.Series, NDArrayA]], Optional[str]]


def _ensure_dense_vector(fn: Callable[..., Vector_name_t]) -> Callable[..., Vector_name_t]:
    @wraps(fn)
    def decorator(self: Any, *args: Any, **kwargs: Any) -> Vector_name_t:
        normalize = kwargs.pop("normalize", False)
        res, fmt = fn(self, *args, **kwargs)
        if res is None:
            return None, None

        if isinstance(res, pd.Series):
            if is_categorical_dtype(res):
                return res, fmt
            if is_string_dtype(res) or is_object_dtype(res) or is_bool_dtype(res):
                return res.astype("category"), fmt
            if is_integer_dtype(res):
                unique = res.unique()
                n_uniq = len(unique)
                if n_uniq <= 2 and (set(unique) & {0, 1}):
                    return res.astype(bool).astype("category"), fmt
                if len(unique) <= len(res) // 100:
                    return res.astype("category"), fmt
            elif not is_numeric_dtype(res):
                raise TypeError(f"Unable to process `pandas.Series` of type `{infer_dtype(res)}`.")
            res = res.to_numpy()
        elif issparse(res):
            if TYPE_CHECKING:
                assert isinstance(res, spmatrix)
            res = res.toarray()
        elif not isinstance(res, (np.ndarray, Sequence)):
            raise TypeError(f"Unable to process result of type `{type(res).__name__}`.")

        res = np.asarray(np.squeeze(res))
        if res.ndim != 1:
            raise ValueError(f"Expected 1-dimensional array, found `{res.ndim}`.")

        return (_min_max_norm(res) if normalize else res), fmt

    return decorator

# patch from marcella
def _set_palette(
    adata: AnnData,
    key: str,
    palette: Optional[str] = None,
    vec: Optional[pd.Series] = None,
) -> dict[Any, Any]:
    if vec is not None:
        if not is_categorical_dtype(vec):
            # vec = vec.astype("category")
            raise TypeError(f"Expected a `categorical` type, found `{infer_dtype(vec)}`.")
    if not is_categorical_dtype(adata.obs[key]):
        raise TypeError(f"Expected a `categorical` type, found `{infer_dtype(adata.obs[key])}`.")
    # luca: quick patch to make the code working, we will need to refactor this anyway. I will call this line manually
    # when constructing the adata object because otherwise when I subeset it there are less categories and the colors are wrong
    # add_colors_for_categorical_sample_annotation(
    #     adata, key=key, force_update_colors=palette is not None, palette=palette
    # )

    return dict(zip(adata.obs[key].cat.categories, [to_rgb(i) for i in adata.uns[Key.uns.colors(key)]]))


def _get_categorical(
    adata: AnnData,
    key: str,
    palette: Optional[str] = None,
    vec: Union[pd.Series, dict[Any, Any], None] = None,
) -> NDArrayA:

    if not isinstance(vec, dict):
        col_dict = _set_palette(adata, key, palette, vec)
    else:
        col_dict = vec
        for cat in vec:
            if cat not in adata.obs[key].cat.categories:
                raise ValueError(
                    f"The key `{cat}` in the given dictionary is not an existing category in anndata[`{key}`]."
                )
            elif not is_color_like(vec[cat]):
                raise ValueError(f"`{vec[cat]}` is not an acceptable color.")

    return np.array([col_dict[v] for v in adata.obs[key]])

# def _get_categorical(
#     adata: AnnData,
#     key: str,
#     palette: Optional[str] = None,
#     vec: Optional[pd.Series] = None,
# ) -> NDArrayA:
#
#     if vec is not None:
#         if not is_categorical_dtype(vec):
#             vec = vec.astype("category")
#             # raise TypeError(f"Expected a `categorical` type, found `{infer_dtype(vec)}`.")
#         if key in adata.obs:
#             logger.debug(f"Overwriting `adata.obs[{key!r}]`.")
#
#         adata.obs[key] = vec.values
#
#     add_colors_for_categorical_sample_annotation(
#         adata, key=key, force_update_colors=palette is not None, palette=palette
#     )
#     col_dict = dict(zip(adata.obs[key].cat.categories, [to_rgb(i) for i in adata.uns[Key.uns.colors(key)]]))
#
#     return np.array([col_dict[v] for v in adata.obs[key]])


def _position_cluster_labels(coords: NDArrayA, clusters: pd.Series, colors: NDArrayA) -> dict[str, NDArrayA]:
    if not is_categorical_dtype(clusters):
        # clusters = clusters.astype("category")
        raise TypeError(f"Expected `clusters` to be `categorical`, found `{infer_dtype(clusters)}`.")

    coords = coords[:, 1:]
    df = pd.DataFrame(coords)
    df["clusters"] = clusters.values
    df = df.groupby("clusters")[[0, 1]].apply(lambda g: list(np.median(g.values, axis=0)))
    df = pd.DataFrame(list(df), index=df.index)
    kdtree = KDTree(coords)
    clusters = np.full(len(coords), fill_value="", dtype=object)
    # index consists of the categories that need not be string
    clusters[kdtree.query(df.values)[1]] = df.index.astype(str)

    # manually patching some of the code for colored text from marcella
    # # napari v0.4.9 - properties must be 1-D in napari/layers/points/points.py:581
    # colors = np.array([to_hex(col) for col in colors])
    # colors = np.array([col if not len(cl) else to_hex((0, 0, 0)) for cl, col in zip(clusters, colors)])
    # return {"clusters": clusters, "colors": colors}

    return {"clusters": clusters}



def _min_max_norm(vec: Union[spmatrix, NDArrayA]) -> NDArrayA:

    if issparse(vec):
        if TYPE_CHECKING:
            assert isinstance(vec, spmatrix)
        vec = vec.toarray().squeeze()
    vec = np.asarray(vec, dtype=np.float64)
    if vec.ndim != 1:
        raise ValueError(f"Expected `1` dimension, found `{vec.ndim}`.")

    maxx, minn = np.nanmax(vec), np.nanmin(vec)

    return (  # type: ignore[no-any-return]
        np.ones_like(vec) if np.isclose(minn, maxx) else ((vec - minn) / (maxx - minn))
    )


@njit(cache=True, fastmath=True)
def _point_inside_triangles(triangles: NDArrayA) -> np.bool_:
    # modified from napari
    AB = triangles[:, 1, :] - triangles[:, 0, :]
    AC = triangles[:, 2, :] - triangles[:, 0, :]
    BC = triangles[:, 2, :] - triangles[:, 1, :]

    s_AB = -AB[:, 0] * triangles[:, 0, 1] + AB[:, 1] * triangles[:, 0, 0] >= 0
    s_AC = -AC[:, 0] * triangles[:, 0, 1] + AC[:, 1] * triangles[:, 0, 0] >= 0
    s_BC = -BC[:, 0] * triangles[:, 1, 1] + BC[:, 1] * triangles[:, 1, 0] >= 0

    return np.any((s_AB != s_AC) & (s_AB == s_BC))


@njit(parallel=True)
def _points_inside_triangles(points: NDArrayA, triangles: NDArrayA) -> NDArrayA:
    out = np.empty(  # type: ignore[var-annotated]
        len(
            points,
        ),
        dtype=np.bool_,
    )
    for i in prange(len(out)):
        out[i] = _point_inside_triangles(triangles - points[i])

    return out


def save_fig(fig: Figure, path: str | Path, make_dir: bool = True, ext: str = "png", **kwargs: Any) -> None:
    """
    Save a figure.

    Parameters
    ----------
    fig
        Figure to save.
    path
        Path where to save the figure. If path is relative, save it under :attr:`scanpy.settings.figdir`.
    make_dir
        Whether to try making the directory if it does not exist.
    ext
        Extension to use if none is provided.
    kwargs
        Keyword arguments for :meth:`matplotlib.figure.Figure.savefig`.

    Returns
    -------
    None
        Just saves the plot.
    """
    if os.path.splitext(path)[1] == "":
        path = f"{path}.{ext}"

    path = Path(path)

    # to avoid the dependency from scanpy
    # if not path.is_absolute():
    #     path = Path(settings.figdir) / path

    if make_dir:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.debug(f"Unable to create directory `{path.parent}`. Reason: `{e}`")

    logger.debug(f"Saving figure to `{path!r}`")

    kwargs.setdefault("bbox_inches", "tight")
    kwargs.setdefault("transparent", True)

    fig.savefig(path, **kwargs)


def _display_channelwise(arr: NDArrayA | da.Array) -> bool:
    n_channels: int = arr.shape[-1]
    if n_channels not in (3, 4):
        return n_channels != 1
    if np.issubdtype(arr.dtype, np.uint8):
        return False  # assume RGB(A)
    if not np.issubdtype(arr.dtype, np.floating):
        return True

    return _not_in_01(arr)


def _get_ellipses_from_circles(centroids: NDArrayA, radii: NDArrayA) -> NDArrayA:
    """Convert circles to ellipses.

    Parameters
    ----------
    centroids
        Centroids of the circles.
    radii
        Radii of the circles.

    Returns
    -------
    NDArrayA
        Ellipses.
    """
    ndim = centroids.shape[1]
    assert ndim == 2
    r = np.stack([radii] * ndim, axis=1)
    lower_left = centroids - r
    upper_right = centroids + r
    r[:, 0] = -r[:, 0]
    lower_right = centroids - r
    upper_left = centroids + r
    ellipses = np.stack([lower_left, lower_right, upper_right, upper_left], axis=1)
    return ellipses
