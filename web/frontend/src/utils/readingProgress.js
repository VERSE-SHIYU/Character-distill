// Resolve the saved page number from backend reading-progress data.
// scroll_position is the exact page when present; otherwise the fraction's
// inverse must match the save formula currentPage/(totalPages-1), not /totalPages.
export function resolveSavedPage(data, totalPages) {
  const sp = data?.scroll_position
  const savedPage = sp !== undefined && sp !== null && sp > 0
    ? sp
    : Math.round((data?.progress || 0) * (totalPages - 1))
  return Math.min(Math.max(0, savedPage), totalPages - 1)
}
