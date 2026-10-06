import React, { useState, useEffect } from 'react';

/**
 * Walk a serialized trie dict to find all doc IDs matching a prefix.
 */
function walkTrie(trieData, prefix) {
  if (!trieData || !prefix) return null;
  const chars = prefix.toLowerCase().split('');
  let node = trieData;
  for (const ch of chars) {
    if (!node[ch]) return new Set();
    node = node[ch];
  }
  return collectIds(node);
}

function collectIds(node) {
  const ids = new Set(node['$'] || []);
  for (const [key, child] of Object.entries(node)) {
    if (key !== '$') {
      for (const id of collectIds(child)) ids.add(id);
    }
  }
  return ids;
}

export default function DocumentSearch({ apiBaseUrl, onResults }) {
  const [query, setQuery] = useState('');
  const [trieData, setTrieData] = useState(null);
  const [isLoaded, setIsLoaded] = useState(false);

  useEffect(() => {
    fetch(`${apiBaseUrl}/api/v1/search/trie`)
      .then(r => r.json())
      .then(data => {
        setTrieData(data.trie);
        setIsLoaded(true);
      })
      .catch(() => setIsLoaded(true));
  }, [apiBaseUrl]);

  useEffect(() => {
    if (!isLoaded) return;
    const trimmed = query.trim();
    if (!trimmed) {
      onResults(null);
      return;
    }
    const words = trimmed.toLowerCase().split(/\s+/);
    let resultSet = null;
    for (const word of words) {
      const ids = walkTrie(trieData, word);
      if (ids === null) continue;
      if (resultSet === null) {
        resultSet = ids;
      } else {
        resultSet = new Set([...resultSet].filter(id => ids.has(id)));
      }
    }
    onResults(resultSet);
  }, [query, trieData, isLoaded, onResults]);

  return (
    <div className="relative w-full">
      <div className="flex items-center gap-2 bg-[#F1EDE6] rounded-xl px-4 py-2.5 border border-transparent focus-within:border-[#C8C1B6] focus-within:bg-[#EBE6DE] transition">
        <svg className="w-4 h-4 text-[#9E9689] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8">
          <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
        </svg>
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="ค้นหาเอกสาร..."
          className="w-full bg-transparent text-[#3D3730] placeholder-[#9E9689] text-sm focus:outline-none"
        />
        {query && (
          <button
            type="button"
            onClick={() => setQuery('')}
            className="text-[#9E9689] hover:text-[#3D3730] text-xs cursor-pointer"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}

function HighlightText({ text, query }) {
  if (!text) return '-';
  if (!query || !query.trim()) return text;

  const words = query.trim().split(/\\s+/).filter(Boolean);
  if (words.length === 0) return text;

  const escaped = words.map(w => w.replace(/[-[\\]{}()*+?.,\\\\^$|#\\s]/g, '\\\\$&')).join('|');
  const regex = new RegExp(`(${escaped})`, 'gi');
  const parts = String(text).split(regex);

  return (
    <span>
      {parts.map((part, index) => {
        const isMatch = words.some(w => w.toLowerCase() === part.toLowerCase());
        return isMatch ? (
          <mark key={index} className="bg-[#F5E6B3] text-[#2B251F] font-medium px-0.5 rounded-xs">
            {part}
          </mark>
        ) : (
          part
        );
      })}
    </span>
  );
}

export { HighlightText };
