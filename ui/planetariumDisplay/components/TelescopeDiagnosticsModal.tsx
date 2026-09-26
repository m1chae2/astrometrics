/**
 * @module TelescopeDiagnosticsModal
 * @fileoverview Modal dialogue displaying decomposed geometric mount pointing models,
 * periodic error harmonics, and declination backlash diagnostics.
 */

import React, { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { MountPointingModel, GuidingSpectrumAnalysis } from '../../common/types/backendTypes';

/**
 * The pointing model as the modal reads it: the backend leaves out terms it did not fit, so a
 * missing term is shown as zero.
 */
type PointingModelForDisplay = Required<MountPointingModel>;

/**
 * The guiding spectrum as the modal reads it: a missing measurement is shown as zero (or empty),
 * except the dominant period and backlash, which stay null when they could not be estimated.
 */
type GuidingSpectrumForDisplay = Required<GuidingSpectrumAnalysis>;

/** The value shown for every pointing term the backend did not report. */
const EMPTY_POINTING_MODEL: PointingModelForDisplay = {
  sampleCount: 0,
  rawRmsArcsec: 0,
  residualRmsArcsec: 0,
  improvementPercent: 0,
  ihArcsec: 0,
  idArcsec: 0,
  meArcsec: 0,
  maArcsec: 0,
  chArcsec: 0,
  tfArcsec: 0,
  totalPolarErrorArcsec: 0,
  confidence: 'insufficient_data',
  message: '',
};

/** The value shown for every guiding measurement the backend did not report. */
const EMPTY_GUIDING_SPECTRUM: GuidingSpectrumForDisplay = {
  sampleCount: 0,
  durationSeconds: 0,
  periodicErrorPeakToPeakArcsec: 0,
  dominantPeriodSeconds: null,
  decBacklashEstimateMs: null,
  peaks: [],
  psdCurve: [],
  message: '',
};

interface Props {
  isOpen: boolean;
  onClose: () => void;
  selectedSessionId: string | null;
}

export const TelescopeDiagnosticsModal: React.FC<Props> = ({
  isOpen,
  onClose,
  selectedSessionId,
}) => {
  const [activeTab, setActiveTab] = useState<'pointing' | 'guiding'>('pointing');
  const [useAllTime, setUseAllTime] = useState<boolean>(!selectedSessionId);
  const [loading, setLoading] = useState<boolean>(false);
  const [pointingModel, setPointingModel] = useState<PointingModelForDisplay | null>(null);
  const [guidingSpectrum, setGuidingSpectrum] = useState<GuidingSpectrumForDisplay | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;

    let active = true;
    const fetchData = async () => {
      setLoading(true);
      setErrorMsg(null);
      try {
        const sid = useAllTime ? undefined : (selectedSessionId || undefined);

        const [pmRes, gsRes] = await Promise.all([
          callBackend('telescope:get_pointing_model', { session_id: sid }),
          callBackend('telescope:get_guiding_spectrum', { session_id: sid }),
        ]);

        if (active) {
          setPointingModel(pmRes ? { ...EMPTY_POINTING_MODEL, ...pmRes } : null);
          setGuidingSpectrum(gsRes ? { ...EMPTY_GUIDING_SPECTRUM, ...gsRes } : null);
        }
      } catch (err: any) {
        if (active) {
          setErrorMsg(err.message || 'Failed to load telescope diagnostics.');
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    fetchData();
    return () => {
      active = false;
    };
  }, [isOpen, selectedSessionId, useAllTime]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-700/80 rounded-xl shadow-2xl w-full max-w-3xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-950/60">
          <div className="flex items-center gap-3">
            <span className="text-xl">🔭</span>
            <div>
              <h2 className="text-lg font-bold text-slate-100">Telescope Performance Diagnostics</h2>
              <p className="text-xs text-slate-400">
                {useAllTime
                  ? 'All-Time Mechanical Model (1,900+ Solves)'
                  : `Session ${selectedSessionId || 'Current'} Performance`}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {selectedSessionId && (
              <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer bg-slate-800 px-2 py-1 rounded border border-slate-700">
                <input
                  type="checkbox"
                  checked={useAllTime}
                  onChange={(e) => setUseAllTime(e.target.checked)}
                  className="rounded text-sky-500 focus:ring-0"
                />
                Fit All Sessions
              </label>
            )}
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Tab Selector */}
        <div className="flex border-b border-slate-800 bg-slate-950/30 px-6 pt-2">
          <button
            onClick={() => setActiveTab('pointing')}
            className={`pb-2.5 px-4 text-xs font-semibold border-b-2 transition-colors ${
              activeTab === 'pointing'
                ? 'border-sky-500 text-sky-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            Mount Pointing Model (TPOINT)
          </button>
          <button
            onClick={() => setActiveTab('guiding')}
            className={`pb-2.5 px-4 text-xs font-semibold border-b-2 transition-colors ${
              activeTab === 'guiding'
                ? 'border-sky-500 text-sky-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            Guiding &amp; Periodic Error (FFT)
          </button>
        </div>

        {/* Content Body */}
        <div className="p-6 overflow-y-auto space-y-6 flex-1 text-slate-200">
          {loading && (
            <div className="py-16 text-center text-slate-400 text-sm animate-pulse">
              Computing least-squares pointing model and frequency spectra...
            </div>
          )}

          {errorMsg && (
            <div className="p-3 bg-red-950/50 border border-red-800/80 rounded-lg text-red-300 text-xs">
              {errorMsg}
            </div>
          )}

          {!loading && activeTab === 'pointing' && pointingModel && (
            <div className="space-y-6">
              {/* RMS Improvement Card */}
              <div className="grid grid-cols-3 gap-4">
                <div className="bg-slate-800/60 border border-slate-700/60 p-4 rounded-lg">
                  <div className="text-xs text-slate-400 uppercase font-semibold">Raw Pointing RMS</div>
                  <div className="text-2xl font-bold text-amber-400 mt-1">
                    {pointingModel.rawRmsArcsec.toFixed(1)}&quot;
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1">{pointingModel.sampleCount} solve points</div>
                </div>
                <div className="bg-slate-800/60 border border-slate-700/60 p-4 rounded-lg">
                  <div className="text-xs text-slate-400 uppercase font-semibold">Model Residual RMS</div>
                  <div className="text-2xl font-bold text-emerald-400 mt-1">
                    {pointingModel.residualRmsArcsec.toFixed(1)}&quot;
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1">
                    {pointingModel.improvementPercent.toFixed(1)}% improvement
                  </div>
                </div>
                <div className="bg-slate-800/60 border border-slate-700/60 p-4 rounded-lg">
                  <div className="text-xs text-slate-400 uppercase font-semibold">Polar Misalignment</div>
                  <div className="text-2xl font-bold text-sky-400 mt-1">
                    {(pointingModel.totalPolarErrorArcsec / 60).toFixed(1)}&apos;
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1">
                    Elev: {pointingModel.meArcsec > 0 ? '+' : ''}{pointingModel.meArcsec.toFixed(0)}&quot; • Az: {pointingModel.maArcsec > 0 ? '+' : ''}{pointingModel.maArcsec.toFixed(0)}&quot;
                  </div>
                </div>
              </div>

              {/* Physical Terms Breakdown */}
              <div className="bg-slate-800/40 border border-slate-700/60 rounded-lg p-5">
                <h3 className="text-sm font-semibold text-slate-200 mb-4 flex items-center gap-2">
                  <span>📐</span> Decomposed Physical Mount Terms
                </h3>
                <div className="grid grid-cols-2 gap-y-4 gap-x-6 text-xs">
                  <div>
                    <div className="flex justify-between text-slate-400 mb-1">
                      <span>Polar Axis Elevation (ME)</span>
                      <span className="font-mono text-slate-200">{pointingModel.meArcsec.toFixed(1)}&quot;</span>
                    </div>
                    <div className="text-[11px] text-slate-500">Altitude tilt of Right Ascension axis.</div>
                  </div>
                  <div>
                    <div className="flex justify-between text-slate-400 mb-1">
                      <span>Polar Axis Azimuth (MA)</span>
                      <span className="font-mono text-slate-200">{pointingModel.maArcsec.toFixed(1)}&quot;</span>
                    </div>
                    <div className="text-[11px] text-slate-500">Azimuthal twist away from true celestial pole.</div>
                  </div>
                  <div>
                    <div className="flex justify-between text-slate-400 mb-1">
                      <span>Cone Non-Orthogonality (CH)</span>
                      <span className="font-mono text-slate-200">{pointingModel.chArcsec.toFixed(1)}&quot;</span>
                    </div>
                    <div className="text-[11px] text-slate-500">Optical tube non-perpendicularity to Declination axis.</div>
                  </div>
                  <div>
                    <div className="flex justify-between text-slate-400 mb-1">
                      <span>Tube Flexure / Gravity Sag (TF)</span>
                      <span className="font-mono text-slate-200">{pointingModel.tfArcsec.toFixed(1)}&quot;</span>
                    </div>
                    <div className="text-[11px] text-slate-500">Mechanical sag across elevation angles.</div>
                  </div>
                  <div>
                    <div className="flex justify-between text-slate-400 mb-1">
                      <span>RA Index Error (IH)</span>
                      <span className="font-mono text-slate-200">{pointingModel.ihArcsec.toFixed(1)}&quot;</span>
                    </div>
                  </div>
                  <div>
                    <div className="flex justify-between text-slate-400 mb-1">
                      <span>Dec Index Error (ID)</span>
                      <span className="font-mono text-slate-200">{pointingModel.idArcsec.toFixed(1)}&quot;</span>
                    </div>
                  </div>
                </div>
              </div>

              {pointingModel.message && (
                <div className="text-xs text-slate-400 italic bg-slate-950/40 p-3 rounded border border-slate-800">
                  {pointingModel.message}
                </div>
              )}
            </div>
          )}

          {!loading && activeTab === 'guiding' && guidingSpectrum && (
            <div className="space-y-6">
              {/* Summary Cards */}
              <div className="grid grid-cols-3 gap-4">
                <div className="bg-slate-800/60 border border-slate-700/60 p-4 rounded-lg">
                  <div className="text-xs text-slate-400 uppercase font-semibold">Periodic Error (Peak-to-Peak)</div>
                  <div className="text-2xl font-bold text-amber-400 mt-1">
                    {guidingSpectrum.periodicErrorPeakToPeakArcsec.toFixed(1)}&quot;
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1">Total mechanical worm unguided runout</div>
                </div>
                <div className="bg-slate-800/60 border border-slate-700/60 p-4 rounded-lg">
                  <div className="text-xs text-slate-400 uppercase font-semibold">Dominant Worm Period</div>
                  <div className="text-2xl font-bold text-sky-400 mt-1">
                    {guidingSpectrum.dominantPeriodSeconds
                      ? `${guidingSpectrum.dominantPeriodSeconds.toFixed(0)}s`
                      : '—'}
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1">Fundamental drive period</div>
                </div>
                <div className="bg-slate-800/60 border border-slate-700/60 p-4 rounded-lg">
                  <div className="text-xs text-slate-400 uppercase font-semibold">DEC Backlash Delay</div>
                  <div className="text-2xl font-bold text-emerald-400 mt-1">
                    {guidingSpectrum.decBacklashEstimateMs
                      ? `${guidingSpectrum.decBacklashEstimateMs.toFixed(0)} ms`
                      : 'Minimal'}
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1">Reversal deadband pulse duration</div>
                </div>
              </div>

              {/* Spectral Harmonics Table */}
              <div className="bg-slate-800/40 border border-slate-700/60 rounded-lg p-5">
                <h3 className="text-sm font-semibold text-slate-200 mb-4 flex items-center gap-2">
                  <span>📊</span> Dominant Mechanical Harmonics (FFT)
                </h3>
                {guidingSpectrum.peaks.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead>
                        <tr className="text-slate-400 border-b border-slate-700/60 pb-2">
                          <th className="pb-2">Period (s)</th>
                          <th className="pb-2">Amplitude</th>
                          <th className="pb-2">Spectral Power</th>
                          <th className="pb-2">Probable Mechanical Source</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800">
                        {guidingSpectrum.peaks.map((p, idx) => (
                          <tr key={idx} className="hover:bg-slate-800/30">
                            <td className="py-2.5 font-mono text-sky-300">{p.periodSeconds}s</td>
                            <td className="py-2.5 font-mono">{p.amplitudeArcsec}&quot;</td>
                            <td className="py-2.5 font-mono">{(p.power * 100).toFixed(1)}%</td>
                            <td className="py-2.5 text-slate-300">{p.probableSource}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="text-xs text-slate-500 py-4 text-center">
                    No prominent cyclic harmonics isolated. Guiding drift is smooth or sample count too low.
                  </div>
                )}
              </div>

              {guidingSpectrum.message && (
                <div className="text-xs text-slate-400 italic bg-slate-950/40 p-3 rounded border border-slate-800">
                  {guidingSpectrum.message}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-slate-800 bg-slate-950/60 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-semibold transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
