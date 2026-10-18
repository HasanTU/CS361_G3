import React, { useState, useEffect, useRef, useCallback } from 'react';

/**
 * DocumentPreviewModal Component
 * Modal overlay with portrait A4 canvas, document switcher (< >), centered pagination, zoom, and download.
 */
const API_BASE_URL = ['null', 'http://localhost:5500', 'http://127.0.0.1:5500'].includes(window.location.origin)
  ? 'http://localhost:5000'
  : window.location.origin;

export function DocumentPreviewModal({ document: doc, isOpen, onClose }) {
  const [currentFileIndex, setCurrentFileIndex] = useState(0);
  const [zoom, setZoom] = useState(100);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(99);
  const [pdfDoc, setPdfDoc] = useState(null);
  const [isPdf, setIsPdf] = useState(false);
  const [isImage, setIsImage] = useState(false);

  const canvasRef = useRef(null);

  const filesList = doc && doc.files && doc.files.length > 0
    ? doc.files
    : doc
    ? [{
        id: doc.id || 'f-1',
        name: doc.file_name || doc.fileName || `${doc.title || 'เอกสาร'}.pdf`,
        url: doc.file_url
          ? new URL(doc.file_url, API_BASE_URL).toString()
          : doc.fileUrl || null,
        file_type: doc.file_type || doc.fileType || '',
        totalPages: doc.totalPages || 99,
      }]
    : [];

  const activeFile = filesList[currentFileIndex] || filesList[0];

  useEffect(() => {
    if (isOpen && doc) {
      setCurrentFileIndex(0);
      setZoom(100);
      setCurrentPage(1);
      loadFilePreview(filesList[0]);
    }
  }, [isOpen, doc]);

  const loadFilePreview = (fileItem) => {
    if (!fileItem) return;
    setZoom(100);
    setCurrentPage(1);
    setTotalPages(fileItem.totalPages || 99);

    const fileSrc = fileItem.url || '';
    const fileType = fileItem.file_type || fileItem.type || '';
    const isPdfFile = fileType === 'application/pdf' || /\.pdf(?:$|[?#])/i.test(fileSrc);
    const isImageFile = /^image\/(png|jpe?g)$/i.test(fileType)
      || /\.(png|jpg|jpeg)(?:$|[?#])/i.test(fileSrc);

    setPdfDoc(null);
    if (isPdfFile && fileSrc) {
      setIsPdf(true);
      setIsImage(false);
      if (window.pdfjsLib) {
        window.pdfjsLib.getDocument(fileSrc).promise.then(
          (pdf) => {
            setPdfDoc(pdf);
            setTotalPages(pdf.numPages);
          },
          () => setPdfDoc(null)
        );
      }
    } else if (isImageFile && fileSrc) {
      setIsPdf(false);
      setIsImage(true);
      setPdfDoc(null);
      setTotalPages(1);
    } else {
      setIsPdf(false);
      setIsImage(false);
      setPdfDoc(null);
    }
  };

  const renderPdfPage = useCallback(async (pageNum, pdf, zoomScale) => {
    if (!pdf || !canvasRef.current) return;
    try {
      const page = await pdf.getPage(pageNum);
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');
      const baseScale = 1.35;
      const scale = baseScale * (zoomScale / 100);
      const viewport = page.getViewport({ scale });
      canvas.height = viewport.height;
      canvas.width = viewport.width;
      await page.render({ canvasContext: context, viewport: viewport }).promise;
    } catch (err) {
      console.error(err);
    }
  }, []);

  useEffect(() => {
    if (isOpen && isPdf && pdfDoc) {
      renderPdfPage(currentPage, pdfDoc, zoom);
    }
  }, [isOpen, isPdf, pdfDoc, currentPage, zoom, renderPdfPage]);

  // Keyboard navigation
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        onClose();
      } else if (e.key === 'ArrowRight' && currentPage < totalPages) {
        setCurrentPage((p) => p + 1);
      } else if (e.key === 'ArrowLeft' && currentPage > 1) {
        setCurrentPage((p) => p - 1);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, currentPage, totalPages, onClose]);

  if (!isOpen || !doc) return null;

  const handleZoomIn = () => setZoom((p) => Math.min(p + 25, 200));
  const handleZoomOut = () => setZoom((p) => Math.max(p - 25, 50));
  const handlePrevPage = () => {
    if (currentPage > 1) setCurrentPage((p) => p - 1);
  };
  const handleNextPage = () => {
    if (currentPage < totalPages) setCurrentPage((p) => p + 1);
  };

  const handlePrevDocument = () => {
    if (currentFileIndex > 0) {
      const nextIndex = currentFileIndex - 1;
      setCurrentFileIndex(nextIndex);
      loadFilePreview(filesList[nextIndex]);
    }
  };

  const handleNextDocument = () => {
    if (currentFileIndex < filesList.length - 1) {
      const nextIndex = currentFileIndex + 1;
      setCurrentFileIndex(nextIndex);
      loadFilePreview(filesList[nextIndex]);
    }
  };

  const handleDownload = (e) => {
    e.stopPropagation();
    const downloadUrl = `${API_BASE_URL}/api/v1/documents/${doc.id}/file?download=true`;
    window.open(downloadUrl, '_blank');
  };

  return (
    <div
      id="preview-modal-overlay"
      className="fixed inset-0 z-50 flex flex-col justify-between bg-black/80 backdrop-blur-xs font-['Prompt',sans-serif] select-none animate-fade-in cursor-pointer"
      onClick={onClose}
    >
      {/* Top Header */}
      <div
        className="w-full flex items-start justify-between p-6 sm:p-8 shrink-0 cursor-pointer"
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div
          className="max-w-2xl text-white space-y-1 pr-4 cursor-default"
          onClick={(e) => e.stopPropagation()}
        >
          <h2 className="text-base sm:text-xl font-normal leading-snug tracking-tight text-white">
            {doc.title || 'ไม่มีชื่อเรื่อง'}
          </h2>
          <p className="text-xs sm:text-sm text-white/80 font-light">
            ได้รับ {doc.document_date || doc.uploaded_at || '-'}
          </p>
          <p className="text-xs sm:text-sm text-white/80 font-light">
            จาก {doc.sender || doc.senderName || '-'}
          </p>
          {filesList.length > 1 && (
            <p className="text-xs text-[#E8E4DC] font-light bg-white/15 inline-block px-3 py-0.5 rounded-full mt-1">
              ไฟล์ที่ {currentFileIndex + 1} จาก {filesList.length}: {activeFile?.name}
            </p>
          )}
        </div>

        <button
          id="btn-modal-close"
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          className="text-white/80 hover:text-white transition p-2 text-2xl sm:text-3xl leading-none cursor-pointer shrink-0"
          title="ปิด (Esc หรือคลิกพื้นหลัง)"
        >
          ✕
        </button>
      </div>

      {/* Center: A4 Paper Preview Canvas with Left & Right Document Switch Buttons */}
      <div
        className="flex-1 flex items-center justify-center p-4 min-h-0 relative cursor-pointer overflow-hidden"
        onClick={onClose}
      >
        {/* Left Switch Button (‹) */}
        {filesList.length > 1 && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              handlePrevDocument();
            }}
            disabled={currentFileIndex <= 0}
            className="absolute left-4 sm:left-12 z-20 w-11 h-11 sm:w-13 sm:h-13 rounded-full bg-white/20 hover:bg-white/30 text-white flex items-center justify-center text-2xl font-bold shadow-xl transition disabled:opacity-20 disabled:cursor-default cursor-pointer backdrop-blur-md"
            title="เอกสารก่อนหน้า"
          >
            ‹
          </button>
        )}

        {/* True Portrait A4 Paper (Height dominant with 1 : 1.4142 portrait ratio) */}
        <div
          className="h-[74vh] max-h-[750px] w-auto aspect-[1/1.414] max-w-[90vw] bg-white rounded shadow-2xl border border-white/20 relative flex items-center justify-center overflow-hidden transition-transform duration-200 cursor-default shrink-0"
          style={{ transform: `scale(${zoom / 100})` }}
          onClick={(e) => e.stopPropagation()}
        >
          {isPdf && pdfDoc ? (
            <canvas ref={canvasRef} className="max-w-full max-h-full object-contain" />
          ) : isImage && activeFile && activeFile.url ? (
            <img src={activeFile.url} alt="Preview" className="max-w-full max-h-full object-contain" />
          ) : (
            <div className="flex flex-col items-center justify-center text-center p-8 text-[#70675D]">
              <span className="text-xl sm:text-2xl font-light tracking-wide text-[#70675D]">
                Preview เอกสาร
              </span>
              {filesList.length > 1 && (
                <span className="text-xs text-[#9E9689] mt-2 font-light">
                  ({activeFile?.name})
                </span>
              )}
            </div>
          )}
        </div>

        {/* Right Switch Button (›) */}
        {filesList.length > 1 && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              handleNextDocument();
            }}
            disabled={currentFileIndex >= filesList.length - 1}
            className="absolute right-4 sm:right-12 z-20 w-11 h-11 sm:w-13 sm:h-13 rounded-full bg-white/20 hover:bg-white/30 text-white flex items-center justify-center text-2xl font-bold shadow-xl transition disabled:opacity-20 disabled:cursor-default cursor-pointer backdrop-blur-md"
            title="เอกสารถัดไป"
          >
            ›
          </button>
        )}
      </div>

      {/* Bottom Control Bar */}
      <div
        className="w-full flex items-center justify-between px-8 sm:px-16 py-5 text-xs sm:text-sm text-white/90 shrink-0 select-none relative cursor-default"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Left: Zoom Controls */}
        <div className="flex items-center gap-2 sm:gap-3 z-10">
          <button
            id="btn-modal-zoom-out"
            type="button"
            onClick={handleZoomOut}
            disabled={zoom <= 50}
            className="hover:text-white px-2.5 py-1 rounded hover:bg-white/15 transition cursor-pointer disabled:opacity-30 disabled:cursor-default text-base font-medium"
            title="ย่อ"
          >
            -
          </button>
          <span className="font-light min-w-[45px] text-center">{zoom}%</span>
          <button
            id="btn-modal-zoom-in"
            type="button"
            onClick={handleZoomIn}
            disabled={zoom >= 200}
            className="hover:text-white px-2.5 py-1 rounded hover:bg-white/15 transition cursor-pointer disabled:opacity-30 disabled:cursor-default text-base font-medium"
            title="ขยาย"
          >
            +
          </button>
        </div>

        {/* Center: Exactly Centered Page Counter */}
        <div className="absolute left-1/2 -translate-x-1/2 flex items-center gap-2 z-10">
          {totalPages > 1 && (
            <button
              type="button"
              onClick={handlePrevPage}
              disabled={currentPage <= 1}
              className="hover:text-white px-2 py-0.5 rounded hover:bg-white/15 transition disabled:opacity-30 cursor-pointer text-sm"
              title="หน้าก่อนหน้า"
            >
              ◀
            </button>
          )}
          <span className="font-light tracking-wide text-center whitespace-nowrap">
            หน้า {currentPage} จาก {totalPages}
          </span>
          {totalPages > 1 && (
            <button
              type="button"
              onClick={handleNextPage}
              disabled={currentPage >= totalPages}
              className="hover:text-white px-2 py-0.5 rounded hover:bg-white/15 transition disabled:opacity-30 cursor-pointer text-sm"
              title="หน้าถัดไป"
            >
              ▶
            </button>
          )}
        </div>

        {/* Right: Download Button */}
        <div className="z-10">
          <button
            id="btn-modal-download"
            type="button"
            onClick={handleDownload}
            className="text-white/80 hover:text-white hover:bg-white/15 p-2 rounded-lg transition cursor-pointer"
            title="ดาวน์โหลดเอกสาร"
            aria-label="ดาวน์โหลดเอกสาร"
          >
            <svg
              className="w-5 h-5 sm:w-6 sm:h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth="1.6"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"
              />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * LecturerDocumentView Component
 */
export default function LecturerDocumentView({
  lecturerName = 'อาจารย์ XXXX',
  facultyName = 'คณะ: yyy'
}) {
  const [documents, setDocuments] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedDocument, setSelectedDocument] = useState(null);
  const [isPreviewModalOpen, setIsPreviewModalOpen] = useState(false);
  const [openMenuId, setOpenMenuId] = useState(null);
  const [editingDoc, setEditingDoc] = useState(null);
  const [editForm, setEditForm] = useState({ title: '', document_date: '', sender: '', receiver: '' });
  const [replacementFile, setReplacementFile] = useState(null);
  const [isSavingEdit, setIsSavingEdit] = useState(false);
  const [editError, setEditError] = useState('');
  const isAdmin = window.currentUser?.role === 'ADMIN';

  const formatThaiDate = (dateStr) => {
    if (!dateStr) return '';
    if (dateStr.includes('น.') || dateStr.includes('ส.ค.') || dateStr.includes('ก.ย.')) {
      return dateStr;
    }
    try {
      const date = new Date(dateStr);
      if (isNaN(date.getTime())) return dateStr;
      const thaiMonths = [
        'ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.',
        'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'
      ];
      const day = date.getDate();
      const month = thaiMonths[date.getMonth()];
      const year = date.getFullYear() + 543;
      const hours = String(date.getHours()).padStart(2, '0');
      const minutes = String(date.getMinutes()).padStart(2, '0');
      return `${day} ${month} ${year} ${hours}:${minutes} น.`;
    } catch {
      return dateStr;
    }
  };

  useEffect(() => {
    let isMounted = true;

    async function fetchDocuments() {
      setIsLoading(true);
      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/documents`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (isMounted) {
          setDocuments(data.documents || []);
        }
      } catch (err) {
        if (isMounted) {
          setDocuments([]);
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    }

    fetchDocuments();
    return () => {
      isMounted = false;
    };
  }, []);

  const handleOpenPreview = (doc) => {
    setSelectedDocument(doc);
    setIsPreviewModalOpen(true);
  };

  const handleClosePreview = () => {
    setIsPreviewModalOpen(false);
    setSelectedDocument(null);
  };

  const handleDownload = (doc) => {
    const downloadUrl = `${API_BASE_URL}/api/v1/documents/${doc.id}/file?download=true`;
    window.open(downloadUrl, '_blank');
  };

  const handleStartEdit = (doc) => {
    setEditForm({
      title: doc.title || '',
      document_date: doc.document_date || '',
      sender: doc.sender || '',
      receiver: doc.receiver || '',
    });
    setReplacementFile(null);
    setEditError('');
    setEditingDoc(doc);
    setOpenMenuId(null);
  };

  const handleSaveEdit = async (event) => {
    event.preventDefault();
    if (!editingDoc) return;

    setIsSavingEdit(true);
    setEditError('');
    const body = new FormData();
    Object.entries(editForm).forEach(([field, value]) => body.append(field, value));
    if (replacementFile) body.append('file', replacementFile);

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/documents/${editingDoc.id}`, {
        method: 'PATCH',
        body,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.message || 'อัปเดตเอกสารไม่สำเร็จ');

      setDocuments((current) => current.map((doc) =>
        doc.id === editingDoc.id ? result.document : doc
      ));
      setEditingDoc(null);
      setReplacementFile(null);
    } catch (error) {
      setEditError(error.message || 'อัปเดตเอกสารไม่สำเร็จ');
    } finally {
      setIsSavingEdit(false);
    }
  };

  return (
    <div className="min-h-screen w-full bg-[#FAF8F5] p-6 sm:p-10 lg:p-14 font-['Prompt',sans-serif] text-[#3D3730]">
      {/* Preview Modal */}
      <DocumentPreviewModal
        document={selectedDocument}
        isOpen={isPreviewModalOpen}
        onClose={handleClosePreview}
      />

      {editingDoc && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={handleSaveEdit} className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
            <div className="mb-5 flex items-center justify-between">
              <h2 className="text-lg font-medium text-[#3D3730]">แก้ไขเอกสาร</h2>
              <button type="button" onClick={() => setEditingDoc(null)} className="rounded-lg p-2 text-[#70675D] hover:bg-[#EFECE6]" aria-label="ปิด">✕</button>
            </div>
            <div className="space-y-3">
              <label className="block text-sm text-[#70675D]">ชื่อเรื่อง
                <input required value={editForm.title} onChange={(e) => setEditForm({ ...editForm, title: e.target.value })} className="mt-1 w-full rounded-lg border border-[#D5CEC4] px-3 py-2 text-[#3D3730]" />
              </label>
              <label className="block text-sm text-[#70675D]">วันที่เอกสาร
                <input required type="date" value={editForm.document_date} onChange={(e) => setEditForm({ ...editForm, document_date: e.target.value })} className="mt-1 w-full rounded-lg border border-[#D5CEC4] px-3 py-2 text-[#3D3730]" />
              </label>
              <label className="block text-sm text-[#70675D]">ผู้ส่ง
                <input required value={editForm.sender} onChange={(e) => setEditForm({ ...editForm, sender: e.target.value })} className="mt-1 w-full rounded-lg border border-[#D5CEC4] px-3 py-2 text-[#3D3730]" />
              </label>
              <label className="block text-sm text-[#70675D]">ผู้รับ
                <input required value={editForm.receiver} onChange={(e) => setEditForm({ ...editForm, receiver: e.target.value })} className="mt-1 w-full rounded-lg border border-[#D5CEC4] px-3 py-2 text-[#3D3730]" />
              </label>
              <label className="block text-sm text-[#70675D]">เปลี่ยนไฟล์ (ไม่เลือกไฟล์เพื่อใช้ไฟล์เดิม)
                <input type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={(e) => setReplacementFile(e.target.files?.[0] || null)} className="mt-1 block w-full text-sm" />
              </label>
              {editError && <p role="alert" className="text-sm text-red-600">{editError}</p>}
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button type="button" onClick={() => setEditingDoc(null)} className="rounded-lg bg-[#EFECE6] px-4 py-2 text-sm text-[#554E45]">ยกเลิก</button>
              <button type="submit" disabled={isSavingEdit} className="rounded-lg bg-[#8C8176] px-4 py-2 text-sm text-white disabled:opacity-60">
                {isSavingEdit ? 'กำลังบันทึก...' : 'บันทึกการแก้ไข'}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="max-w-6xl mx-auto">
        <div className="mb-6 sm:mb-8">
          <h1 className="text-2xl sm:text-3xl font-normal text-[#4A433B] tracking-tight mb-1">
            {lecturerName}
          </h1>
          <p className="text-xs sm:text-sm text-[#70675D] font-light">
            {facultyName}
          </p>
        </div>

        <div className="w-full bg-white rounded-2xl shadow-xs border border-[#E8E4DC] p-5 sm:p-8 min-h-[480px] flex flex-col justify-start">
          <div className="w-full grid grid-cols-12 pb-3 mb-3 border-b border-[#EFECE6] text-xs sm:text-sm text-[#70675D] font-normal select-none">
            <div className="col-span-12 md:col-span-4 pl-3">ชื่อเรื่อง</div>
            <div className="hidden md:block md:col-span-3 text-left">วันที่ได้รับ</div>
            <div className="hidden md:block md:col-span-2 text-left">ผู้รับ</div>
            <div className="hidden md:block md:col-span-2 text-left">ผู้ส่ง</div>
            <div className="col-span-12 md:col-span-1 text-right pr-2"></div>
          </div>

          {isLoading ? (
            <div className="space-y-3 w-full animate-pulse">
              {[1, 2, 3].map((item) => (
                <div
                  key={item}
                  className="w-full grid grid-cols-12 items-center p-3 sm:p-4 rounded-xl border border-[#EFECE6] bg-[#FAF8F5]/40"
                >
                  <div className="col-span-12 md:col-span-4 pr-4"><div className="h-4 sm:h-5 bg-[#D5CEC4]/70 rounded-full w-4/5"></div></div>
                  <div className="hidden md:block md:col-span-3 pr-4"><div className="h-4 sm:h-5 bg-[#D5CEC4]/70 rounded-full w-3/4"></div></div>
                  <div className="hidden md:block md:col-span-2 pr-4"><div className="h-4 sm:h-5 bg-[#D5CEC4]/70 rounded-full w-2/3"></div></div>
                  <div className="hidden md:block md:col-span-2 pr-4"><div className="h-4 sm:h-5 bg-[#D5CEC4]/70 rounded-full w-2/3"></div></div>
                  <div className="hidden md:flex md:col-span-1 justify-end gap-2 pr-2">
                    <div className="w-5 h-5 rounded-full bg-[#D5CEC4]/50"></div>
                    <div className="w-5 h-5 rounded-full bg-[#D5CEC4]/50"></div>
                  </div>
                </div>
              ))}
            </div>
          ) : documents.length === 0 ? (
            <div className="w-full flex-1 min-h-[320px] flex items-center justify-center text-center">
              <p className="text-lg sm:text-xl font-light text-[#9E9689] select-none">
                ไม่มีเอกสารเข้าสู่ระบบของท่าน
              </p>
            </div>
          ) : (
            <div className="space-y-2.5 w-full">
              {documents.map((doc) => (
                <div
                  key={doc.id || doc._id || doc.title}
                  className="w-full grid grid-cols-12 items-center p-3 sm:p-4 rounded-xl border border-[#EFECE6] bg-white hover:bg-[#FAF8F5] transition duration-150 group"
                >
                  <div className="col-span-12 md:col-span-4 pl-3 pr-4">
                    <span
                      onClick={() => handleOpenPreview(doc)}
                      className="text-xs sm:text-sm text-[#3D3730] hover:text-[#7A7067] font-normal leading-relaxed break-words cursor-pointer"
                    >
                      {doc.title || 'ไม่มีชื่อเรื่อง'}
                    </span>
                    {doc.files && doc.files.length > 1 && (
                      <span className="ml-2 text-[10px] text-[#70675D] bg-[#EFECE6] px-1.5 py-0.5 rounded-md font-light">
                        {doc.files.length} ไฟล์
                      </span>
                    )}
                  </div>
                  <div className="col-span-6 md:col-span-3 text-xs sm:text-sm text-[#70675D] font-light mt-2 md:mt-0 pl-3 md:pl-0">
                    <span className="md:hidden text-[#9E9689] block text-[11px]">วันที่: </span>
                    {formatThaiDate(doc.document_date || doc.uploaded_at)}
                  </div>
                  <div className="col-span-6 md:col-span-2 text-xs sm:text-sm text-[#70675D] font-light mt-2 md:mt-0">
                    <span className="md:hidden text-[#9E9689] block text-[11px]">ผู้รับ: </span>
                    {doc.receiver || '-'}
                  </div>
                  <div className="col-span-6 md:col-span-2 text-xs sm:text-sm text-[#70675D] font-light mt-2 md:mt-0 pl-3 md:pl-0">
                    <span className="md:hidden text-[#9E9689] block text-[11px]">ผู้ส่ง: </span>
                    {doc.sender || '-'}
                  </div>
                  <div className="col-span-6 md:col-span-1 flex items-center justify-end gap-3 pr-2 mt-2 md:mt-0">
                    <button
                      type="button"
                      onClick={() => handleOpenPreview(doc)}
                      className="text-[#70675D] hover:text-[#3D3730] hover:bg-[#EFECE6] p-1.5 rounded-lg transition cursor-pointer"
                      title="ดูตัวอย่างเอกสาร"
                    >
                      <svg className="w-4 h-4 sm:w-5 sm:h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.6">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                      </svg>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDownload(doc)}
                      className="text-[#70675D] hover:text-[#3D3730] hover:bg-[#EFECE6] p-1.5 rounded-lg transition cursor-pointer"
                      title="ดาวน์โหลดเอกสาร"
                    >
                      <svg className="w-4 h-4 sm:w-5 sm:h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.6">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                      </svg>
                    </button>
                    {isAdmin && (
                      <div className="relative">
                        <button
                          type="button"
                          onClick={() => setOpenMenuId(openMenuId === doc.id ? null : doc.id)}
                          className="rounded-lg p-1.5 text-[#70675D] hover:bg-[#EFECE6] hover:text-[#3D3730]"
                          aria-label={`ตัวเลือกเอกสาร ${doc.title}`}
                          aria-haspopup="menu"
                          aria-expanded={openMenuId === doc.id}
                        >
                          <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                            <circle cx="5" cy="12" r="1.8" />
                            <circle cx="12" cy="12" r="1.8" />
                            <circle cx="19" cy="12" r="1.8" />
                          </svg>
                        </button>
                        {openMenuId === doc.id && (
                          <div role="menu" className="absolute right-0 top-full z-20 mt-1 min-w-28 rounded-lg border border-[#E8E4DC] bg-white p-1 shadow-lg">
                            <button type="button" role="menuitem" onClick={() => handleStartEdit(doc)} className="w-full rounded-md px-3 py-2 text-left text-sm text-[#3D3730] hover:bg-[#F5F2EC]">
                              แก้ไขเอกสาร
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
