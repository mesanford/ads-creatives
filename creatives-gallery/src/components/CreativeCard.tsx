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

export default function CreativeCard({ creative }: { creative: Creative }) {
  const isVideo = creative.firebase_storage_url.endsWith('.mp4') || creative.creative_type === 'YOUTUBE_VIDEO';

  return (
    <div className="bg-white rounded-lg shadow-md overflow-hidden border border-gray-200 hover:shadow-lg transition-shadow duration-300">
      <div className="relative h-48 bg-gray-100 flex items-center justify-center overflow-hidden">
        {creative.firebase_storage_url !== 'N/A' ? (
          isVideo ? (
            <video 
              src={creative.firebase_storage_url} 
              className="w-full h-full object-contain"
              controls
              muted
            />
          ) : (
            <img 
              src={creative.firebase_storage_url} 
              alt={creative.ad_name} 
              className="w-full h-full object-contain"
            />
          )
        ) : (
          <div className="text-gray-400 italic">No Media Available</div>
        )}
        <div className="absolute top-2 right-2 px-2 py-1 bg-black bg-opacity-60 text-white text-xs font-bold rounded">
          {creative.platform}
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
          {creative.final_url !== 'N/A' && (
            <a 
              href={creative.final_url} 
              target="_blank" 
              rel="noopener noreferrer"
              className="text-blue-600 hover:text-blue-800 text-xs font-medium"
            >
              View Landing Page →
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
