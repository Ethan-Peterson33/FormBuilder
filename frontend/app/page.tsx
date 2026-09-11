"use client";

import { useRef, useState } from "react";
import html2canvas from "html2canvas";
import jsPDF from "jspdf";

type ImageItem = {
  name: string;
  url: string;
};

export default function HomePage() {
  const [htmlBlob, setHtmlBlob] = useState(`
    <h1>Test PDF</h1>
    <p>This HTML came from the form.</p>
  `);

  const [loading, setLoading] = useState(false);

  const pdfContentRef = useRef<HTMLDivElement>(null);

  const waitForImages = async (element: HTMLElement) => {
    const images = Array.from(element.querySelectorAll("img"));

    await Promise.all(
      images.map((img) => {
        if (img.complete) {
          return Promise.resolve();
        }

        return new Promise<void>((resolve) => {
          img.onload = () => resolve();
          img.onerror = () => resolve();
        });
      })
    );
  };

  const handleGeneratePdf = async () => {
    try {
      setLoading(true);

      // 1. Ask Next.js for the image list
      const response = await fetch("/api/images");

      if (!response.ok) {
        throw new Error("Failed to fetch images");
      }

      const data: {
        count: number;
        images: ImageItem[];
      } = await response.json();

      // 2. Build HTML for the images
      const imagesHtml = data.images
        .map(
          (image) => `
            <div style="margin-top: 20px;">
              <img
                src="${image.url}"
                alt="${image.name}"
                style="
                  max-width: 100%;
                  height: auto;
                  display: block;
                "
              />
            </div>
          `
        )
        .join("");

      // 3. Combine the supplied HTML with the images
      const combinedHtml = `
        <div style="
          font-family: Arial, sans-serif;
          padding: 30px;
          background: white;
          color: black;
        ">
          ${htmlBlob}

          ${imagesHtml}
        </div>
      `;

      const element = pdfContentRef.current;

      if (!element) {
        throw new Error("PDF container was not found");
      }

      // Put the HTML into our hidden rendering area
      element.innerHTML = combinedHtml;

      // 4. Wait until proxied images finish loading
      await waitForImages(element);

      // 5. Render HTML to canvas
      const canvas = await html2canvas(element, {
        scale: 2,
        useCORS: true,
        backgroundColor: "#ffffff",
      });

      const imageData = canvas.toDataURL("image/jpeg", 0.95);

      // 6. Create A4 PDF
      const pdf = new jsPDF({
        orientation: "portrait",
        unit: "mm",
        format: "a4",
      });

      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();

      const imageWidth = pageWidth;
      const imageHeight =
        (canvas.height * imageWidth) / canvas.width;

      let heightLeft = imageHeight;
      let position = 0;

      pdf.addImage(
        imageData,
        "JPEG",
        0,
        position,
        imageWidth,
        imageHeight
      );

      heightLeft -= pageHeight;

      // Add more pages when content is taller than one page
      while (heightLeft > 0) {
        position = heightLeft - imageHeight;

        pdf.addPage();

        pdf.addImage(
          imageData,
          "JPEG",
          0,
          position,
          imageWidth,
          imageHeight
        );

        heightLeft -= pageHeight;
      }

      // 7. Download it
      pdf.save("document.pdf");
    } catch (error) {
      console.error("PDF generation failed:", error);
      alert("PDF generation failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main style={{ padding: "30px" }}>
      <h1>PDF Test</h1>

      <p>Enter the HTML that should appear in the PDF:</p>

      <textarea
        value={htmlBlob}
        onChange={(e) => setHtmlBlob(e.target.value)}
        rows={14}
        style={{
          width: "100%",
          padding: "10px",
          fontFamily: "monospace",
        }}
      />

      <button
        onClick={handleGeneratePdf}
        disabled={loading}
        style={{
          marginTop: "16px",
          padding: "10px 20px",
          cursor: "pointer",
        }}
      >
        {loading ? "Building PDF..." : "Download PDF"}
      </button>

      {/* Needs to exist in the DOM for html2canvas.
          We move it off screen instead of display:none. */}
      <div
        ref={pdfContentRef}
        style={{
          position: "absolute",
          left: "-10000px",
          top: 0,
          width: "794px",
          background: "white",
        }}
      />
    </main>
  );
}