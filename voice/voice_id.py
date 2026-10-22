"""Voice ID: Zephyr only acts for the voice she has learned (yours), and ignores everyone else.

Model: CAM++ (3D-Speaker, trained on VoxCeleb), run locally with sherpa-onnx. Each utterance
becomes a 512-number "voiceprint"; voices are compared by cosine similarity (1.0 = identical).
Measured on this laptop: ~0.05 s per check. Nothing leaves the machine.

Your samples are kept in voiceprint.npz next to this file (excluded from git).
"""
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, 'models', 'speaker', 'campplus_en.onnx')
PROFILE = os.path.join(HERE, 'voiceprint.npz')

# What Zephyr asks you to say when learning your voice: a mix of commands and normal speech,
# so she learns how you sound both calling her and chatting.
ENROL_PHRASES = [
    'Zephyr, turn on the TV and open YouTube.',
    'Set the volume to twelve and switch to Art Mode.',
    "What's a good movie to watch tonight?",
    'Zephyr, play something relaxing on Spotify.',
    'The quick brown fox jumps over the lazy dog by the river.',
    "I'm heading to bed now, turn everything off please.",
]


class VoiceID:
    def __init__(self, rate=16000):
        import sherpa_onnx
        self.rate = rate
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=MODEL, num_threads=2))
        self.samples, self.threshold = None, None
        if os.path.exists(PROFILE):
            p = np.load(PROFILE)
            self.samples, self.threshold = p['samples'], float(p['threshold'])

    @property
    def enrolled(self):
        return self.samples is not None and len(self.samples) > 0

    def embed(self, pcm16):
        x = pcm16.astype(np.float32) / 32768
        s = self.extractor.create_stream()
        s.accept_waveform(self.rate, x)
        s.input_finished()
        v = np.array(self.extractor.compute(s), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def score(self, pcm16):
        """How much this sounds like you: the mean of the 3 closest enrolled samples."""
        sims = np.sort(self.samples @ self.embed(pcm16))[::-1]
        return float(sims[:3].mean())

    def is_you(self, pcm16):
        if not self.enrolled or len(pcm16) < self.rate * 0.6:      # too short to judge: let it through
            return True, None
        s = self.score(pcm16)
        return s >= self.threshold, s

    def enrol(self, clips):
        """clips: your recordings of ENROL_PHRASES. The bar is set from how alike your own clips are
        (each against the others), with a safety margin, so it fits your voice and this mic."""
        embs = np.stack([self.embed(c) for c in clips])
        own = []
        for i in range(len(embs)):
            others = np.delete(embs, i, axis=0)
            own.append(np.sort(others @ embs[i])[::-1][:3].mean())
        self.threshold = float(np.clip(np.min(own) - 0.12, 0.30, 0.65))
        self.samples = embs
        np.savez(PROFILE, samples=embs, threshold=self.threshold)
        return float(np.mean(own)), self.threshold

    def learn(self, pcm16, score, min_score=0.62, keep=40):
        """Adds a clear match (your voice through this mic, in this room, with whatever's playing)
        to the voiceprint, so it keeps getting better at recognising you. Only confident, long
        enough clips count, so others can't drift it; the original samples are always kept."""
        if score < min_score or len(pcm16) < self.rate * 1.5:
            return
        self.samples = np.vstack([self.samples, self.embed(pcm16)])
        if len(self.samples) > keep:
            self.samples = np.vstack([self.samples[:6], self.samples[-(keep - 6):]])
        np.savez(PROFILE, samples=self.samples, threshold=self.threshold)

    def forget(self):
        self.samples, self.threshold = None, None
        if os.path.exists(PROFILE):
            os.remove(PROFILE)
