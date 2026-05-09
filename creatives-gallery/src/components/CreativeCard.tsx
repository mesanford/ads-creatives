import React from 'react';

interface Creative {
  ad_id: string;
  platform: string;
  ad_name: string;
  headline: string;
  ad_text: string;
  firebase_storage_url: string;
  final_url: string;
  creative_type?: string;
}

export type ViewMode = 'card' | 'compact' | 'expanded';

function MediaBlock({
  creative,
  className,
}: {
  creative: Creative;
  className: string;
}) {
  const isYouTube = creative.firebase_storage_url.startsWith('https://www.youtube.com/embed/');
  const isVideo = !isYouTube && creative.firebase_storage_url.endsWith('.mp4');

  if (creative.firebase_storage_url === 'N/A') {
    return (
      <div className={`${className} flex items-center justify-center bg-gray-100 text-gray-400 italic text-xs`}>
        No Media
      </div>
    );
  }
  if (isYouTube) {
    return (
      <iframe
        src={creative.firebase_storage_url}
        className={className}
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowFullScreen
      />
    );
  }
  if (isVideo) {
    return (
      <video
        src={creative.firebase_storage_url}
        className={`${className} object-contain`}
        controls
        muted
      />
    );
  }
  return (
    <img
      src={creative.firebase_storage_url}
      alt={creative.ad_name}
      className={`${className} object-contain`}
    />
  );
}

function PlatformBadge({ platform, size = 'sm' }: { platform: string; size?: 'xs' | 'sm' }) {
  return (
    <span
      className={`inline-flex items-center bg-gray-900 text-white font-bold rounded ${
        size === 'xs' ? 'text-[9px] px-1.5 py-0.5' : 'text-[10px] px-2 py-1'
      }`}
    >
      {platform}
    </span>
  );
}

function LandingLink({ url }: { url: string }) {
  if (url === 'N/A') return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-600 hover:text-blue-800 text-xs font-medium whitespace-nowrap"
    >
      View →
    </a>
  );
}

// ── Card view (default grid) ──────────────────────────────────────────────────
function CardView({ creative }: { creative: Creative }) {
  return (
    <div className="bg-white rounded-lg shadow-md overflow-hidden border border-gray-200 hover:shadow-lg transition-shadow duration-300">
      <div className="relative h-48 bg-gray-100 flex items-center justify-center overflow-hidden">
        <MediaBlock creative={creative} className="w-full h-full" />
        <div className="absolute top-2 right-2">
          <PlatformBadge platform={creative.platform} />
        </div>
      </div>

      <div className="p-4">
        <h3 className="text-sm font-semibold text-gray-900 line-clamp-1 mb-1">
          {creative.headline !== 'N/A' ? creative.headline : creative.ad_name}
        </h3>
        <p className="text-xs text-gray-600 line-clamp-3 h-12 mb-3">
          {creative.ad_text !== 'N/A' ? creative.ad_text : 'No description available.'}
        </p>

        <div className="flex justify-between items-center pt-2 border-t border-gray-100">
          <span className="text-[10px] text-gray-400 uppercase font-medium">ID: {creative.ad_id}</span>
          <LandingLink url={creative.final_url} />
        </div>
      </div>
    </div>
  );
}

// ── Compact list row ──────────────────────────────────────────────────────────
function CompactView({ creative }: { creative: Creative }) {
  const headline = creative.headline !== 'N/A' ? creative.headline : creative.ad_name;
  const body = creative.ad_text !== 'N/A' ? creative.ad_text : null;

  return (
    <div className="bg-white border border-gray-200 rounded-lg px-4 py-3 flex items-center gap-4 hover:bg-gray-50 transition-colors">
      {/* Thumbnail */}
      <div className="w-12 h-12 flex-shrink-0 rounded overflow-hidden bg-gray-100">
        <MediaBlock creative={creative} className="w-full h-full" />
      </div>

      {/* Text */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-900 truncate">{headline}</p>
        {body && <p className="text-xs text-gray-500 truncate mt-0.5">{body}</p>}
      </div>

      {/* Meta */}
      <div className="flex items-center gap-3 flex-shrink-0">
        <PlatformBadge platform={creative.platform} size="xs" />
        <span className="text-[10px] text-gray-400 hidden sm:block">ID: {creative.ad_id}</span>
        <LandingLink url={creative.final_url} />
      </div>
    </div>
  );
}

// ── Expanded list row ─────────────────────────────────────────────────────────
function ExpandedView({ creative }: { creative: Creative }) {
  const headline = creative.headline !== 'N/A' ? creative.headline : creative.ad_name;

  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden hover:shadow-md transition-shadow flex gap-0">
      {/* Image panel */}
      <div className="w-72 flex-shrink-0 bg-gray-100 flex items-center justify-center">
        <MediaBlock creative={creative} className="w-full h-full min-h-52 max-h-72 object-contain" />
      </div>

      {/* Content panel */}
      <div className="flex-1 p-6 flex flex-col justify-between min-w-0">
        <div>
          <div className="flex items-start justify-between gap-3 mb-3">
            <h3 className="text-base font-semibold text-gray-900 leading-snug">{headline}</h3>
            <PlatformBadge platform={creative.platform} />
          </div>

          {creative.ad_text !== 'N/A' && (
            <p className="text-sm text-gray-700 leading-relaxed line-clamp-6">{creative.ad_text}</p>
          )}

          {creative.ad_name && creative.ad_name !== headline && (
            <p className="text-xs text-gray-400 mt-3">Ad name: {creative.ad_name}</p>
          )}
        </div>

        <div className="flex items-center justify-between pt-4 border-t border-gray-100 mt-4">
          <span className="text-xs text-gray-400 font-medium">ID: {creative.ad_id}</span>
          <LandingLink url={creative.final_url} />
        </div>
      </div>
    </div>
  );
}

// ── Public export ─────────────────────────────────────────────────────────────
export default function CreativeCard({
  creative,
  view = 'card',
}: {
  creative: Creative;
  view?: ViewMode;
}) {
  if (view === 'compact') return <CompactView creative={creative} />;
  if (view === 'expanded') return <ExpandedView creative={creative} />;
  return <CardView creative={creative} />;
}
