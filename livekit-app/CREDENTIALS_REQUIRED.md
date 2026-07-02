# Required API Credentials & Subscriptions

To run the AI Interview App, the following active accounts, API keys, and configurations are required from third-party services. Please provide these to the development team.

## 1. LiveKit (Real-time Video & Audio Infrastructure)
LiveKit handles the WebRTC connections, room management, and routing the audio/video between the frontend and the backend agent.
* **Service:** [LiveKit Cloud](https://cloud.livekit.io/)
* **Required Keys:**
  * `LIVEKIT_URL`: The WebSocket URL for your project (e.g., `wss://my-project-xxxx.livekit.cloud`).
  * `LIVEKIT_API_KEY`: The API key to authenticate the server.
  * `LIVEKIT_API_SECRET`: The secret key to sign access tokens.

## 2. LiveAvatar by HeyGen (The Visual Avatar)
LiveAvatar provides the real-time talking head that syncs with the generated speech.
* **Service:** [HeyGen](https://app.heygen.com/)
* **Required Keys:**
  * `LIVEAVATAR_API_KEY`: The API key from the HeyGen dashboard.
  * `LIVEAVATAR_AVATAR_ID`: The specific ID of the avatar to be used in the interview.

## 3. Google Gemini (The Brain / LLM)
Gemini will act as the interviewer, generating the questions and responses.
* **Service:** [Google AI Studio](https://aistudio.google.com/)
* **Required Keys:**
  * `GEMINI_API_KEY`: The API key to access the Gemini models (recommended: `gemini-2.5-flash`).

## 4. Deepgram (Speech-to-Text / STT)
Deepgram is used for ultra-fast, real-time transcription of candidate speech.
* **Service:** [Deepgram Console](https://console.deepgram.com/)
* **Required Keys:**
  * `DEEPGRAM_API_KEY`: The API key to authenticate transcription requests.

## 5. ElevenLabs (Text-to-Speech / TTS)
ElevenLabs provides ultra-realistic, low-latency voices for the avatar.
* **Service:** [ElevenLabs](https://elevenlabs.io/)
* **Required Keys:**
  * `ELEVENLABS_API_KEY`: The API key for voice generation.
  * *(Optional)* **Voice ID**: If a specific custom voice is desired, the Voice ID from the dashboard.

---

**Environment File Integration:**
Once obtained, these keys should be placed inside the `backend/.env` file. See `backend/.env.example` for the formatting structure.