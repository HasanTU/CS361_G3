import React, { useState, useRef, useEffect, useCallback } from 'react';

const API_BASE_URL = ['null', 'http://localhost:5500', 'http://127.0.0.1:5500'].includes(window.location.origin)
  ? 'http://localhost:5000'
  : window.location.origin;

export default function StaffDocumentIngestion() {
  const [formData, setFormData] = useState({
    title: '',
    receivedDate: new Date().toISOString().split('T')[0],
    recipientName: '',
    senderName: ''
  });
  const [recipientUsers, setRecipientUsers] = useState([]);
  const [showRecipientSuggestions, setShowRecipientSuggestions] = useState(false);

  // Multiple files state: array of { id, file, name, ext, url, isUnsupported }
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [activeFileId, setActiveFileId] = useState(null);

  // PDF Preview & Canvas state
  const [isPdf, setIsPdf] = useState(false);
  const [isImage, setIsImage] = useState(false);
  const [pdfDoc, setPdfDoc] = useState(null);
  const [isPreviewActive, setIsPreviewActive] = useState(false);

  // Zoom & Pan state
  const [zoom, setZoom] = useState(100);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [toast, setToast] = useState({ show: false, type: '', title: '', subtitle: '' });

  const fileInputRef = useRef(null);
  const canvasRef = useRef(null);
  const containerRef = useRef(null);

  const allowedExtensions = ['pdf', 'png', 'jpg', 'jpeg'];

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/v1/auth/users`, { credentials: 'include' })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error('โหลดรายชื่อผู้รับไม่สำเร็จ')))
      .then((data) => setRecipientUsers(data.users || []))
      .catch(() => setRecipientUsers([]));
  }, []);

  const handleInputChange = (field, value) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  const showToast = (type, title, subtitle = '') => {
    setToast({ show: true, type, title, subtitle });
    setTimeout(() => {
      setToast({ show: false, type: '', title: '', subtitle: '' });
    }, 4000);
  };

  const renderPdfPage = useCallback(async (pageNumber, doc) => {
    if (!doc || !canvasRef.current) return;
    try {
      const page = await doc.getPage(pageNumber);
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');
      const baseScale = 1.35;
      const viewport = page.getViewport({ scale: baseScale });

      canvas.height = viewport.height;
      canvas.width = viewport.width;

      await page.render({ canvasContext: context, viewport: viewport }).promise;
    } catch (err) {
      console.error('Error rendering PDF page:', err);
    }
  }, []);

  const processFiles = (newFiles) => {
    if (!newFiles || newFiles.length === 0) return;

    const fileList = Array.from(newFiles);
    let hasInvalid = false;

    const processedList = fileList.map((file, idx) => {
      const parts = file.name.split('.');
      const ext = parts.length > 1 ? parts.pop().toLowerCase() : '';
      const isValid = allowedExtensions.includes(ext);
      if (!isValid) hasInvalid = true;

      return {
        id: `${Date.now()}-${idx}-${Math.random().toString(36).substr(2, 9)}`,
        file: file,
        name: file.name,
        ext: ext,
        url: isValid ? URL.createObjectURL(file) : null,
        isUnsupported: !isValid
      };
    });

    setUploadedFiles(prev => [...prev, ...processedList]);

    if (hasInvalid) {
      showToast('error', 'Error บันทึกไม่สำเร็จ', 'กรอกรายละเอียดไม่ครบ/ไฟล์ไม่ถูกต้อง');
    }

    const firstValid = processedList.find(f => !f.isUnsupported);
    if (firstValid) {
      loadDocumentPreview(firstValid);
    }
  };

  const loadDocumentPreview = async (item) => {
    if (!item || item.isUnsupported) return;

    setActiveFileId(item.id);
    setIsPreviewActive(true);
    setZoom(100);
    setPanOffset({ x: 0, y: 0 });
    setCurrentPage(1);

    if (item.ext === 'pdf') {
      setIsPdf(true);
      setIsImage(false);
      try {
        if (window.pdfjsLib) {
          const arrayBuffer = await item.file.arrayBuffer();
          const doc = await window.pdfjsLib.getDocument({ data: arrayBuffer }).promise;
          setPdfDoc(doc);
          setTotalPages(doc.numPages);
          renderPdfPage(1, doc);
        }
      } catch (pdfErr) {
        setTotalPages(1);
      }
    } else {
      setIsPdf(false);
      setIsImage(true);
      setPdfDoc(null);
      setTotalPages(1);
    }
  };

  useEffect(() => {
    if (isPdf && pdfDoc && isPreviewActive) {
      renderPdfPage(currentPage, pdfDoc);
    }
  }, [isPdf, pdfDoc, currentPage, isPreviewActive, renderPdfPage]);

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      processFiles(e.target.files);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDragOver = (e) => e.preventDefault();
  const handleDrop = (e) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      processFiles(e.dataTransfer.files);
    }
  };

  const handleRemoveFile = (idToRemove, e) => {
    e.stopPropagation();
    setUploadedFiles(prev => {
      const filtered = prev.filter(f => f.id !== idToRemove);
      if (activeFileId === idToRemove) {
        const nextValid = filtered.find(f => !f.isUnsupported);
        if (nextValid) {
          loadDocumentPreview(nextValid);
        } else {
          setIsPreviewActive(false);
          setActiveFileId(null);
          setPdfDoc(null);
        }
      }
      return filtered;
    });
  };

  const handleZoomIn = () => setZoom(prev => Math.min(prev + 25, 200));
  const handleZoomOut = () => setZoom(prev => Math.max(prev - 25, 50));
  const handleClosePreview = () => {
    setIsPreviewActive(false);
    setActiveFileId(null);
    setPanOffset({ x: 0, y: 0 });
  };

  const handlePrevPage = () => {
    if (currentPage > 1) setCurrentPage(prev => prev - 1);
  };
  const handleNextPage = () => {
    if (currentPage < totalPages) setCurrentPage(prev => prev + 1);
  };

  const handleMouseDown = (e) => {
    if (!isPreviewActive) return;
    setIsDragging(true);
    setDragStart({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
  };

  const handleMouseMove = (e) => {
    if (!isDragging) return;
    setPanOffset({
      x: e.clientX - dragStart.x,
      y: e.clientY - dragStart.y
    });
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const hasAnyUnsupported = uploadedFiles.some(f => f.isUnsupported);
  const isRecipientValid = recipientUsers.some((user) => user.name === formData.recipientName.trim());
  const recipientSuggestions = recipientUsers.filter((user) =>
    user.name.toLowerCase().includes(formData.recipientName.trim().toLowerCase())
  );
  const isFormValid =
    formData.title.trim() !== '' &&
    formData.receivedDate.trim() !== '' &&
    isRecipientValid &&
    formData.senderName.trim() !== '' &&
    uploadedFiles.length > 0 &&
    !hasAnyUnsupported;

  const handleSubmit = async (e) => {
    if (e) e.preventDefault();
    if (!isFormValid) {
      showToast('error', 'Error บันทึกไม่สำเร็จ', 'กรอกรายละเอียดไม่ครบ/ไฟล์ไม่ถูกต้อง');
      return;
    }

    setIsSubmitting(true);

    const payload = new FormData();
    payload.append('title', formData.title);
    payload.append('document_date', formData.receivedDate);
    payload.append('receiver', formData.recipientName);
    payload.append('sender', formData.senderName);
    payload.append('file', uploadedFiles[0].file);

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/documents`, {
        method: 'POST',
        credentials: 'include',
        body: payload
      });

      if (response.ok) {
        showToast('success', 'บันทึกสำเร็จ');
        resetForm();
      } else {
        const errorData = await response.json().catch(() => ({}));
        showToast(
          'error',
          'Error บันทึกไม่สำเร็จ',
          errorData.error?.message || `HTTP ${response.status}`
        );
      }
    } catch (error) {
      showToast('error', 'Error บันทึกไม่สำเร็จ', 'ไม่สามารถเชื่อมต่อกับเซิร์ฟเวอร์ได้');
    }

    setIsSubmitting(false);
  };

  const resetForm = () => {
    setFormData({
      title: '',
      receivedDate: new Date().toISOString().split('T')[0],
      recipientName: '',
      senderName: ''
    });
    setUploadedFiles([]);
    setActiveFileId(null);
    setIsPdf(false);
    setIsImage(false);
    setPdfDoc(null);
    setIsPreviewActive(false);
    setPanOffset({ x: 0, y: 0 });
    setZoom(100);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const activeFile = uploadedFiles.find(f => f.id === activeFileId);

  return (
    <div className="min-h-screen w-full bg-[#FAF8F5] flex flex-col md:flex-row font-['Prompt',sans-serif] text-[#3D3730] relative overflow-x-hidden">
      {/* Toast Alert */}
      {toast.show && (
        <div className="fixed top-6 left-1/2 -translate-x-1/2 z-50 transition-all duration-300 transform scale-100">
          {toast.type === 'success' ? (
            <div className="bg-[#6BBF72] text-white px-8 py-3.5 rounded-2xl shadow-lg flex items-center gap-3 min-w-[260px] justify-center">
              <span className="text-xl font-bold flex items-center justify-center w-6 h-6 border-2 border-white rounded-full text-xs">✓</span>
              <span className="text-base font-normal tracking-wide">{toast.title}</span>
            </div>
          ) : (
            <div className="bg-[#F87171] text-white px-7 py-3 rounded-2xl shadow-lg flex items-center gap-4 min-w-[320px]">
              <span className="text-2xl font-light leading-none">✕</span>
              <div className="flex flex-col">
                <span className="text-sm font-medium">{toast.title}</span>
                <span className="text-xs text-white/90 font-light">{toast.subtitle}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Left Form Area with Sticky Bottom Action */}
      <div className="w-full md:w-[35%] lg:w-[33%] h-screen bg-[#FAF8F5] p-6 sm:p-10 flex flex-col justify-between border-r border-[#EFECE6] shrink-0 sticky top-0">
        <div className="overflow-y-auto pr-1 flex-1 space-y-6">
          <h1 className="text-2xl sm:text-3xl font-normal text-[#4A433B]">เจ้าหน้าที่</h1>

          <div>
            <label className="block text-sm text-[#70675D] mb-2 font-normal">รายละเอียดเอกสาร*</label>
            <div className="space-y-3">
              <input
                type="text"
                placeholder="ชื่อเรื่อง/เลขที่เอกสาร*"
                value={formData.title}
                onChange={e => handleInputChange('title', e.target.value)}
                className="w-full bg-[#F1EDE6] text-[#3D3730] placeholder-[#9E9689] px-4 py-3 rounded-xl border border-transparent focus:border-[#C8C1B6] focus:bg-[#EBE6DE] focus:outline-none transition text-sm"
              />

              <input
                type="date"
                placeholder="วันที่รับ*"
                value={formData.receivedDate}
                onChange={e => handleInputChange('receivedDate', e.target.value)}
                className="w-full bg-[#F1EDE6] text-[#3D3730] placeholder-[#9E9689] px-4 py-3 rounded-xl border border-transparent focus:border-[#C8C1B6] focus:bg-[#EBE6DE] focus:outline-none transition text-sm"
              />

              <div className="relative">
                <input
                  type="text"
                  placeholder="อาจารย์ผู้รับ*"
                  value={formData.recipientName}
                  onChange={e => handleInputChange('recipientName', e.target.value)}
                  onFocus={() => setShowRecipientSuggestions(true)}
                  onBlur={() => setTimeout(() => setShowRecipientSuggestions(false), 120)}
                  className="w-full bg-[#F1EDE6] text-[#3D3730] placeholder-[#9E9689] px-4 py-3 rounded-xl border border-transparent focus:border-[#C8C1B6] focus:bg-[#EBE6DE] focus:outline-none transition text-sm"
                />
                {showRecipientSuggestions && recipientUsers.length > 0 && (
                  <div className="absolute left-0 right-0 top-full z-30 mt-1 max-h-48 overflow-y-auto rounded-xl border border-[#E5E0D8] bg-white py-1 shadow-lg">
                    {recipientSuggestions.length > 0 ? recipientSuggestions.map((user) => (
                      <button
                        key={user.name}
                        type="button"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => {
                          handleInputChange('recipientName', user.name);
                          setShowRecipientSuggestions(false);
                        }}
                        className="flex w-full items-center px-3 py-2 text-left text-sm text-[#554E45] transition hover:bg-[#F5F2EC]"
                      >
                        <span className="truncate">{user.name}</span>
                      </button>
                    )) : <p className="px-3 py-2 text-xs text-[#9E9689]">ไม่พบชื่อผู้รับ</p>}
                  </div>
                )}
                <p className={`mt-1 text-xs font-light ${formData.recipientName && !isRecipientValid ? 'text-red-500' : 'text-[#9E9689]'}`}>
                  {recipientUsers.length > 0 ? 'เลือกชื่อจากรายชื่อผู้ใช้งานในระบบ' : 'กำลังโหลดรายชื่อผู้ใช้งาน...'}
                </p>
              </div>

              <input
                type="text"
                placeholder="ผู้ส่ง/หน่วยงาน*"
                value={formData.senderName}
                onChange={e => handleInputChange('senderName', e.target.value)}
                className="w-full bg-[#F1EDE6] text-[#3D3730] placeholder-[#9E9689] px-4 py-3 rounded-xl border border-transparent focus:border-[#C8C1B6] focus:bg-[#EBE6DE] focus:outline-none transition text-sm"
              />
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between">
              <label className="block text-sm text-[#70675D] font-normal">ไฟล์เอกสาร*</label>
              {uploadedFiles.length > 0 && (
                <span className="text-xs text-[#70675D]">({uploadedFiles.length} ไฟล์)</span>
              )}
            </div>

            <p className="text-xs text-red-500 font-light mt-1 mb-2">
              * รองรับเฉพาะไฟล์ .pdf, .png, .jpg, .jpeg เท่านั้น
            </p>

            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileChange}
              accept=".pdf,.png,.jpg,.jpeg,*"
              multiple
              className="hidden"
            />

            <div className="flex flex-wrap items-center gap-3">
              <div
                onClick={() => fileInputRef.current && fileInputRef.current.click()}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                className="w-24 h-32 rounded-xl border border-[#D5CEC4] bg-white flex flex-col items-center justify-center cursor-pointer hover:border-[#8C8176] hover:bg-[#FAF8F5] transition group shrink-0"
                title="คลิกหรือลากไฟล์มาเพิ่ม (.pdf, .png, .jpg)"
              >
                <span className="text-3xl text-[#9E9689] group-hover:text-[#6B6257] font-light transition">+</span>
                <span className="text-[10px] text-[#9E9689] mt-1">เพิ่มไฟล์</span>
              </div>

              {uploadedFiles.map((item) => {
                const isActive = activeFileId === item.id;
                return (
                  <div
                    key={item.id}
                    className={`w-24 h-32 rounded-xl border bg-white flex flex-col items-center justify-between p-2.5 shadow-sm shrink-0 transition relative group ${
                      isActive ? 'border-[#8C8176] ring-1 ring-[#8C8176]' : 'border-[#E5E0D8]'
                    }`}
                  >
                    <button
                      type="button"
                      onClick={(e) => handleRemoveFile(item.id, e)}
                      className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-[#E08A8A] hover:bg-[#C97272] text-white rounded-full flex items-center justify-center text-[10px] font-bold shadow transition cursor-pointer"
                      title="ลบไฟล์นี้"
                    >
                      ✕
                    </button>

                    <span className="text-[11px] text-[#70675D] font-normal text-center truncate w-full pt-1" title={item.name}>
                      {item.name}
                    </span>

                    {item.isUnsupported ? (
                      <div className="w-full border border-[#E08A8A] bg-[#FFF5F5] rounded-md py-1 px-1 text-center">
                        <span className="text-[10px] text-[#D9534F] font-light block leading-tight">เอกสารไม่ถูกต้อง</span>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => loadDocumentPreview(item)}
                        className={`w-full border rounded-md py-1 px-1 text-center transition cursor-pointer ${
                          isActive
                            ? 'bg-[#8C8176] text-white border-[#8C8176]'
                            : 'border-[#C8C1B6] hover:border-[#8C8176] hover:bg-[#F5F2EC] text-[#554E45]'
                        }`}
                      >
                        <span className="text-[11px] font-light">
                          {isActive ? 'กำลังดู' : 'ดูเอกสาร'}
                        </span>
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Sticky Bottom Action Button */}
        <div className="pt-4 pb-2 mt-auto border-t border-[#EFECE6] bg-[#FAF8F5] shrink-0 sticky bottom-0">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={isSubmitting || !isFormValid}
            className={`w-full py-3.5 px-4 rounded-xl font-normal text-sm transition duration-200 flex items-center justify-center gap-2 ${
              isSubmitting
                ? 'bg-[#8C8176] text-white opacity-90 cursor-wait'
                : isFormValid
                ? 'bg-[#8C8176] hover:bg-[#7A7067] text-white shadow-sm cursor-pointer'
                : 'bg-[#EFECE6] text-[#A39B90] cursor-not-allowed'
            }`}
          >
            {isSubmitting ? (
              <>
                <svg className="animate-spin h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span>กำลังบันทึกเอกสาร...</span>
              </>
            ) : (
              <span>บันทึกเอกสาร</span>
            )}
          </button>
        </div>
      </div>

      {/* Right Canvas Area with Pan & Zoom */}
      <div
        className="w-full md:w-[65%] lg:w-[67%] h-screen max-h-screen bg-[#F4F2EE] flex flex-col items-center justify-start pt-3 sm:pt-4 pb-3 px-4 relative overflow-hidden select-none"
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
      >
        <div className="h-[84vh] max-h-[820px] w-auto aspect-[1/1.414] max-w-full flex flex-col items-center">
          {/* Top Canvas Controls */}
          <div className="w-full flex items-center justify-between text-xs text-[#70675D] px-1 py-1 mb-1 shrink-0">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleZoomOut}
                disabled={zoom <= 50}
                className="hover:text-black px-2 py-1 rounded hover:bg-[#EAE6DE] cursor-pointer disabled:opacity-30"
                title="ย่อ"
              >
                -
              </button>
              <span className="min-w-[42px] text-center font-light">{zoom}%</span>
              <button
                type="button"
                onClick={handleZoomIn}
                disabled={zoom >= 200}
                className="hover:text-black px-2 py-1 rounded hover:bg-[#EAE6DE] cursor-pointer disabled:opacity-30"
                title="ขยาย"
              >
                +
              </button>
              {zoom !== 100 && (
                <button
                  type="button"
                  onClick={() => { setZoom(100); setPanOffset({ x: 0, y: 0 }); }}
                  className="text-[10px] text-[#70675D] hover:text-black underline ml-1 cursor-pointer"
                  title="รีเซ็ตขนาด"
                >
                  รีเซ็ต
                </button>
              )}
            </div>

            <div className="flex items-center gap-2">
              {totalPages > 1 && (
                <button
                  type="button"
                  onClick={handlePrevPage}
                  disabled={currentPage <= 1}
                  className="hover:text-black px-1.5 py-0.5 rounded hover:bg-[#EAE6DE] disabled:opacity-30 cursor-pointer"
                >
                  ◀
                </button>
              )}
              <span>หน้า {currentPage} จาก {totalPages}</span>
              {totalPages > 1 && (
                <button
                  type="button"
                  onClick={handleNextPage}
                  disabled={currentPage >= totalPages}
                  className="hover:text-black px-1.5 py-0.5 rounded hover:bg-[#EAE6DE] disabled:opacity-30 cursor-pointer"
                >
                  ▶
                </button>
              )}
            </div>

            <button
              type="button"
              onClick={handleClosePreview}
              className="text-[#70675D] hover:text-black hover:bg-[#EAE6DE] px-2 py-1 rounded cursor-pointer"
              title="ปิดการดูตัวอย่าง"
            >
              ✕
            </button>
          </div>

          {/* True Portrait A4 Canvas Container */}
          <div
            ref={containerRef}
            onMouseDown={handleMouseDown}
            className={`w-full flex-1 bg-white rounded shadow-sm border border-[#E5E0D8] relative overflow-hidden flex items-center justify-center ${
              isPreviewActive ? (isDragging ? 'cursor-grabbing' : 'cursor-grab') : ''
            }`}
          >
            {isPreviewActive && activeFile && !activeFile.isUnsupported ? (
              <div
                className="w-full h-full flex items-center justify-center p-2 transition-transform duration-75 origin-center"
                style={{
                  transform: `translate(${panOffset.x}px, ${panOffset.y}px) scale(${zoom / 100})`
                }}
              >
                {isPdf ? (
                  <canvas ref={canvasRef} className="max-w-full max-h-full object-contain shadow-xs bg-white pointer-events-none" />
                ) : isImage && activeFile.url ? (
                  <img
                    src={activeFile.url}
                    alt="Preview"
                    className="max-w-full max-h-full object-contain shadow-xs pointer-events-none"
                  />
                ) : null}
              </div>
            ) : (
              <span className="text-2xl sm:text-3xl font-light tracking-wide text-[#70675D]">
                Preview เอกสาร
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
