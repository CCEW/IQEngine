import React from 'react';

import Band from './band';

export const MultipleSelection = ({ selected, handleSelection, bands }) => {
  return (
    <div className="flex flex-wrap justify-center gap-x-2">
      {Object.keys(bands).map((band, i) => (
        <Band key={i} handleSelection={handleSelection} selected={selected} band={bands[band]} />
      ))}
    </div>
  );
};

export default MultipleSelection;
