from pypdf import PdfReader

reader = PdfReader("Пример Курсовая работа по БД.pdf")
print(f"Total pages: {len(reader.pages)}")

# Print first 5 pages' text to inspect layout, structure, and headings
with open("pdf_structure.txt", "w", encoding="utf-8") as f:
    f.write("=== Outline / Table of Contents ===\n")
    try:
        outline = reader.outline
        def print_outline(elem, depth=0):
            if isinstance(elem, list):
                for sub in elem:
                    print_outline(sub, depth + 1)
            else:
                f.write(f"{'  ' * depth}- {elem.title} (page {reader.get_destination_page_number(elem) + 1})\n")
        print_outline(outline)
    except Exception as e:
        f.write(f"Could not read outline: {str(e)}\n")

    f.write("\n=== Page Content ===\n")
    # Let's dump text from pages 1 to 10 to see structure
    for i in range(min(12, len(reader.pages))):
        f.write(f"\n--- PAGE {i+1} ---\n")
        f.write(reader.pages[i].extract_text())

print("PDF text successfully exported to pdf_structure.txt!")
