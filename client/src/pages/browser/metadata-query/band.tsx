import React from 'react';

export const Band = ({ handleSelection, selected, band }) => {
  return (
    <label className="cursor-pointer label flex items-center gap-2 px-1">
      <input
        onChange={() => handleSelection(band)}
        type="checkbox"
        checked={selected === band[0] ? true : false}
        className="checkbox checkbox-success flex-none"
      />
      <span className="label-text whitespace-nowrap">{band[0]}</span>
    </label>
  );
};

export default Band;
