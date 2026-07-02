import React, { useEffect, useState } from 'react';
import { 
  LiveKitRoom, 
  RoomAudioRenderer, 
  VideoTrack,
  useTracks,
  AudioVisualizer,
  DisconnectButton,
  useRoomContext
} from '@livekit/components-react';
import { Track, RoomEvent, TranscriptionSegment, Participant } from 'livekit-client';
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
        
        <div style={{ flex: 1, display: 'flex', justifyContent: 'center', alignItems: 'center', position: 'relative' }}>
          <ActiveAvatar />
          <TranscriptOverlay />
        </div>
        
        <RoomFooter />

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
  const videoTracks = useTracks([Track.Source.Camera]);
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

const TranscriptOverlay = () => {
  const room = useRoomContext();
  const [transcripts, setTranscripts] = useState<{name: string, text: string}[]>([]);

  useEffect(() => {
    const handleTranscription = (segments: TranscriptionSegment[], participant?: Participant) => {
      const text = segments.map(s => s.text).join(' ');
      if (text.trim()) {
        const name = participant?.name || participant?.identity || 'Agent';
        setTranscripts(prev => {
          const newT = [...prev, { name, text }];
          return newT.slice(-3); // keep only the last 3 messages for the overlay
        });
      }
    };
    
    room.on(RoomEvent.TranscriptionReceived, handleTranscription);
    return () => {
      room.off(RoomEvent.TranscriptionReceived, handleTranscription);
    };
  }, [room]);

  if (transcripts.length === 0) return null;

  return (
    <div style={{ 
      position: 'absolute', 
      bottom: '20px', 
      left: '50%', 
      transform: 'translateX(-50%)',
      backgroundColor: 'rgba(0,0,0,0.7)', 
      padding: '1rem', 
      borderRadius: '8px',
      width: '80%',
      maxWidth: '600px',
      textAlign: 'left'
    }}>
      {transcripts.map((t, i) => (
        <div key={i} style={{ marginBottom: i === transcripts.length - 1 ? 0 : '0.5rem' }}>
          <strong style={{ color: t.name === 'Agent' ? '#4dabf7' : '#fff' }}>{t.name}:</strong> {t.text}
        </div>
      ))}
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
