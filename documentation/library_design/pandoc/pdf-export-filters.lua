-- Centers table header cells for PDF export while leaving body column
-- alignment (left-aligned prose/code) untouched, since GFM's per-column
-- alignment markers apply to header and body together and can't be split.
--
-- Any column headed "Step" (the numbered pipeline-sequence tables) is fully
-- centered, header and body — those are short single-digit labels that read
-- better centered, unlike the prose/code columns next to them. Note: Pandoc's
-- LaTeX writer only honors per-cell alignment overrides on header cells; body
-- cells always follow the table's column spec, so the "Step" column has to be
-- centered via colspecs, not by setting body cell.alignment (which is a
-- silent no-op for body rows).
function Table(tbl)
  local head = tbl.head
  local stepColumns = {}
  for _, row in ipairs(head.rows) do
    for colIndex, cell in ipairs(row.cells) do
      cell.alignment = "AlignCenter"
      if pandoc.utils.stringify(cell.contents):match("^%s*Step%s*$") then
        stepColumns[colIndex] = true
      end
    end
  end
  for colIndex, colspec in ipairs(tbl.colspecs) do
    if stepColumns[colIndex] then
      tbl.colspecs[colIndex] = {"AlignCenter", colspec[2]}
    end
  end
  return tbl
end

-- GitHub renders raw <br> tags inside table cells as line breaks, but
-- Pandoc's LaTeX writer drops unrecognized raw HTML inline nodes silently,
-- so "In: ...<br>Out: ..." collapses into one run-on line with no
-- separator at all. Convert them to real line breaks for every output format.
function RawInline(el)
  if el.format == "html" and el.text:match("^<br%s*/?>$") then
    return pandoc.LineBreak()
  end
  return el
end
