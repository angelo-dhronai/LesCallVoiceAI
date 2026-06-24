/** Minimal inline audio player; lazy-loads the WAV only when rendered. */
export function AudioPlayer({ src }: { src: string }) {
  return (
    <audio
      controls
      preload="none"
      src={src}
      className="h-9 w-full max-w-[260px]"
      style={{ colorScheme: 'normal' }}
    />
  )
}
