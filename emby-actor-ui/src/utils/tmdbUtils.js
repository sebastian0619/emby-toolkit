// src/utils/tmdbUtils.js
// 简单的TMDb图片URL构建工具

export function getTmdbImageUrl(imagePath, size = 'w300', baseUrl = 'https://image.tmdb.org/t/p') {
  if (!imagePath) return '';
  return `${baseUrl}/${size}${imagePath}`;
}
