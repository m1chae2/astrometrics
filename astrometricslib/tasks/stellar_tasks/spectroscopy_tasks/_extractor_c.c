#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define MAX_SAMPLES 256
#define MAX_LM_ITER 50
#define LM_EPS 1e-7

static double median_of_array(double *arr, int count) {
    if (count <= 0) return 0.0;
    if (count == 1) return arr[0];

    double temp[4];
    for (int i = 0; i < count && i < 4; i++) temp[i] = arr[i];

    for (int i = 0; i < count - 1; i++) {
        for (int j = i + 1; j < count; j++) {
            if (temp[i] > temp[j]) {
                double tmp = temp[i];
                temp[i] = temp[j];
                temp[j] = tmp;
            }
        }
    }

    if (count % 2 == 1) return temp[count / 2];
    return 0.5 * (temp[count / 2 - 1] + temp[count / 2]);
}

// Solve 3x3 linear system A * x = b via Cramer's rule
static int solve_3x3(const double A[3][3], const double b[3], double x[3]) {
    double detA = A[0][0] * (A[1][1] * A[2][2] - A[1][2] * A[2][1])
                - A[0][1] * (A[1][0] * A[2][2] - A[1][2] * A[2][0])
                + A[0][2] * (A[1][0] * A[2][1] - A[1][1] * A[2][0]);
    if (fabs(detA) < 1e-15) return 0;

    double detA0 = b[0] * (A[1][1] * A[2][2] - A[1][2] * A[2][1])
                 - A[0][1] * (b[1] * A[2][2] - A[1][2] * b[2])
                 + A[0][2] * (b[1] * A[2][1] - A[1][1] * b[2]);

    double detA1 = A[0][0] * (b[1] * A[2][2] - A[1][2] * b[2])
                 - b[0] * (A[1][0] * A[2][2] - A[1][2] * A[2][0])
                 + A[0][2] * (A[1][0] * b[2] - b[1] * A[2][0]);

    double detA2 = A[0][0] * (A[1][1] * b[2] - b[1] * A[2][1])
                 - A[0][1] * (A[1][0] * b[2] - b[1] * A[2][0])
                 + b[0] * (A[1][0] * A[2][1] - A[1][1] * A[2][0]);

    x[0] = detA0 / detA;
    x[1] = detA1 / detA;
    x[2] = detA2 / detA;
    return 1;
}

static PyObject* fit_cross_section_gaussian_c(PyObject *self, PyObject *args) {
    PyArrayObject *data_obj = NULL;
    PyObject *center_obj = NULL;
    PyObject *perp_obj = NULL;
    double search_radius = 0.0;

    if (!PyArg_ParseTuple(args, "O!OOD", &PyArray_Type, &data_obj, &center_obj, &perp_obj, &search_radius)) {
        return NULL;
    }

    double center_x = 0.0, center_y = 0.0;
    double perp_x = 0.0, perp_y = 0.0;

    if (!PyArg_ParseTuple(center_obj, "dd", &center_x, &center_y)) {
        PyErr_Clear();
        return NULL;
    }
    if (!PyArg_ParseTuple(perp_obj, "dd", &perp_x, &perp_y)) {
        PyErr_Clear();
        return NULL;
    }

    if (PyArray_NDIM(data_obj) != 2) {
        Py_RETURN_NONE;
    }

    npy_intp height = PyArray_DIM(data_obj, 0);
    npy_intp width = PyArray_DIM(data_obj, 1);

    int radius_int = (int)search_radius;
    int max_points = 2 * radius_int + 1;
    if (max_points > MAX_SAMPLES) max_points = MAX_SAMPLES;

    double offsets[MAX_SAMPLES];
    double values[MAX_SAMPLES];
    int n_valid = 0;

    for (int i = -radius_int; i <= radius_int; i++) {
        double x = center_x + i * perp_x;
        double y = center_y + i * perp_y;
        int px = (int)lround(x);
        int py = (int)lround(y);

        if (px >= 0 && px < width && py >= 0 && py < height) {
            offsets[n_valid] = (double)i;
            void *ptr = PyArray_GETPTR2(data_obj, py, px);
            double val = 0.0;
            int type = PyArray_TYPE(data_obj);

            if (type == NPY_DOUBLE) val = *(double*)ptr;
            else if (type == NPY_FLOAT) val = (double)*(float*)ptr;
            else if (type == NPY_INT64) val = (double)*(int64_t*)ptr;
            else val = PyArray_PyIntAsInt(PyObject_GetItem((PyObject*)data_obj, Py_BuildValue("(ii)", py, px)));

            values[n_valid] = val;
            n_valid++;
        }
    }

    if (n_valid < 5) {
        Py_RETURN_NONE;
    }

    int edge_count = n_valid / 2;
    if (edge_count > 2) edge_count = 2;

    double edge_buf[4];
    int edge_n = 0;
    for (int i = 0; i < edge_count; i++) edge_buf[edge_n++] = values[i];
    for (int i = n_valid - edge_count; i < n_valid; i++) edge_buf[edge_n++] = values[i];

    double bg = median_of_array(edge_buf, edge_n);
    double bg_sub[MAX_SAMPLES];
    double max_amp = -1e30;
    int max_idx = 0;

    for (int i = 0; i < n_valid; i++) {
        bg_sub[i] = values[i] - bg;
        if (bg_sub[i] > max_amp) {
            max_amp = bg_sub[i];
            max_idx = i;
        }
    }

    if (max_amp <= 0.0) {
        Py_RETURN_NONE;
    }

    double p[3];
    p[0] = max_amp;
    p[1] = offsets[max_idx];
    p[2] = search_radius / 3.0;

    double lambda_lm = 0.001;

    for (int iter = 0; iter < MAX_LM_ITER; iter++) {
        double J[MAX_SAMPLES][3];
        double r[MAX_SAMPLES];
        double rss = 0.0;

        double A = p[0];
        double mu = p[1];
        double sig = p[2];

        if (sig <= 1e-6) sig = 1e-6;

        for (int i = 0; i < n_valid; i++) {
            double dx = offsets[i] - mu;
            double z = dx / sig;
            double exp_val = exp(-0.5 * z * z);
            double f_val = A * exp_val;

            r[i] = bg_sub[i] - f_val;
            rss += r[i] * r[i];

            J[i][0] = exp_val;
            J[i][1] = f_val * (dx / (sig * sig));
            J[i][2] = f_val * (dx * dx / (sig * sig * sig));
        }

        double H[3][3] = {{0}};
        double g[3] = {0};

        for (int i = 0; i < n_valid; i++) {
            for (int j = 0; j < 3; j++) {
                g[j] += J[i][j] * r[i];
                for (int k = 0; k < 3; k++) {
                    H[j][k] += J[i][j] * J[i][k];
                }
            }
        }

        double H_damped[3][3];
        memcpy(H_damped, H, sizeof(H));
        for (int j = 0; j < 3; j++) {
            H_damped[j][j] *= (1.0 + lambda_lm);
            if (H_damped[j][j] == 0.0) H_damped[j][j] = 1e-6;
        }

        double dp[3];
        if (!solve_3x3(H_damped, g, dp)) {
            break;
        }

        double p_trial[3] = {p[0] + dp[0], p[1] + dp[1], p[2] + dp[2]};
        double rss_trial = 0.0;
        double A_t = p_trial[0];
        double mu_t = p_trial[1];
        double sig_t = p_trial[2];

        if (sig_t > 0) {
            for (int i = 0; i < n_valid; i++) {
                double dx = offsets[i] - mu_t;
                double z = dx / sig_t;
                double res = bg_sub[i] - (A_t * exp(-0.5 * z * z));
                rss_trial += res * res;
            }
        } else {
            rss_trial = rss + 1.0;
        }

        if (rss_trial < rss) {
            p[0] = p_trial[0];
            p[1] = p_trial[1];
            p[2] = p_trial[2];
            lambda_lm /= 10.0;
            if (fabs(rss - rss_trial) < LM_EPS) break;
        } else {
            lambda_lm *= 10.0;
            if (lambda_lm > 1e10) break;
        }
    }

    double mu_fit = p[1];
    double sig_fit = p[2];

    if (!isnan(mu_fit) && !isnan(sig_fit) && !isinf(mu_fit) && !isinf(sig_fit)) {
        if (sig_fit > 0.0 && sig_fit <= search_radius * 2.0 && fabs(mu_fit) <= search_radius) {
            return Py_BuildValue("(dd)", mu_fit, sig_fit);
        }
    }

    Py_RETURN_NONE;
}

static PyMethodDef ExtractorMethods[] = {
    {"fit_cross_section_gaussian_c", fit_cross_section_gaussian_c, METH_VARARGS, "Fit 1D Gaussian cross section in C"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef extractormodule = {
    PyModuleDef_HEAD_INIT,
    "_extractor_c",
    "CPython C Extension for fast spectral cross section fitting",
    -1,
    ExtractorMethods
};

PyMODINIT_FUNC PyInit__extractor_c(void) {
    import_array();
    return PyModule_Create(&extractormodule);
}
