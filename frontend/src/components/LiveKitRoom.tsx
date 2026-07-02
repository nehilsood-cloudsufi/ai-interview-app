import React from 'react';
import { 
  LiveKitRoom, 
  RoomAudioRenderer, 
  VideoTrack,
  useTracks,
  AudioVisualizer,
  DisconnectButton
} from '@livekit/components-react';
import { Track } from 'livekit-client';
import '@livekit/components-styles';
import { useInterview } from '../context/InterviewContext';

export const InterviewRoom: React.FC = () => {
  const { token, setStage } = useInterview();

  if (!token) return null;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <LiveKitRoom
        video={false}
        audio={true}
        token={token}
        serverUrl={import.meta.env.VITE_LIVEKIT_URL}
        connect={true}
        onDisconnected={() => setStage('completed')}
        style={{ flex: 1, display: 'flex', flexDirection: 'column', backgroundColor: '#111', color: 'white' }}
      >
        <RoomHeader />
        
        <div style={{ flex: 1, display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
          <ActiveAvatar />
        </div>
        
        <RoomFooter />

        {/* This component ensures we hear the remote audio */}
        <RoomAudioRenderer />
      </LiveKitRoom>
    </div>
  );
};

const RoomHeader = () => {
  return (
    <div style={{ padding: '1rem', display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #333' }}>
      <h2>Interview in Progress</h2>
      <DisconnectButton>End Interview</DisconnectButton>
    </div>
  );
};

const ActiveAvatar = () => {
  // Find all remote video tracks (the avatar)
  const videoTracks = useTracks([Track.Source.Camera]);
  // Find all remote audio tracks (for visualization)
  const audioTracks = useTracks([Track.Source.Microphone]);

  const avatarTrack = videoTracks.find(t => t.participant.identity !== 'local');
  const agentAudio = audioTracks.find(t => t.participant.identity !== 'local');

  if (!avatarTrack) {
    return (
      <div style={{ textAlign: 'center' }}>
        <p>Waiting for interviewer to join...</p>
        <div className="spinner"></div>
      </div>
    );
  }

  return (
    <div style={{ width: '100%', maxWidth: '600px', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ borderRadius: '8px', overflow: 'hidden', backgroundColor: '#000', aspectRatio: '16/9' }}>
        <VideoTrack trackRef={avatarTrack} />
      </div>
      
      {agentAudio && (
        <div style={{ height: '50px', backgroundColor: '#222', borderRadius: '4px' }}>
          <AudioVisualizer trackRef={agentAudio} />
        </div>
      )}
    </div>
  );
};

const RoomFooter = () => {
  return (
    <div style={{ padding: '1rem', borderTop: '1px solid #333', textAlign: 'center' }}>
      <p>Microphone is active. Speak clearly into your mic.</p>
    </div>
  );
};
