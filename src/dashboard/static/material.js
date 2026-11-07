// A picture's material sphere: once <model-viewer> has loaded the sphere (/generation/material-sphere.glb),
// its one material gets the picture's maps. glTF reads occlusion, roughness and metallic from the red, green
// and blue channels of one texture, so the three grey maps are packed into one on a canvas first. Every
// texture repeats, so the seamless maps tile around the sphere.
(function () {
  'use strict';
  var viewer = document.querySelector('model-viewer[data-material]');
  if (!viewer) return;
  var maps = JSON.parse(viewer.dataset.material);
  var note = document.getElementById('gen-material-note');
  var REPEAT = 10497;

  function image(url) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error('could not load ' + url)); };
      img.src = url;
    });
  }

  function packed(urls) {
    return Promise.all(urls.map(image)).then(function (imgs) {
      var w = imgs[0].naturalWidth, h = imgs[0].naturalHeight;
      var canvas = document.createElement('canvas');
      canvas.width = w; canvas.height = h;
      var ctx = canvas.getContext('2d', { willReadFrequently: true });
      var channels = imgs.map(function (img) {
        ctx.drawImage(img, 0, 0, w, h);
        return ctx.getImageData(0, 0, w, h).data;
      });
      var out = ctx.createImageData(w, h);
      for (var i = 0; i < out.data.length; i += 4) {
        out.data[i] = channels[0][i]; out.data[i + 1] = channels[1][i]; out.data[i + 2] = channels[2][i]; out.data[i + 3] = 255;
      }
      ctx.putImageData(out, 0, 0);
      return new Promise(function (resolve) { canvas.toBlob(function (blob) { resolve(URL.createObjectURL(blob)); }, 'image/png'); });
    });
  }

  function put(slot, texture) {
    slot.setTexture(texture);
    var sampler = slot.texture && slot.texture.sampler;
    if (sampler) { sampler.setWrapS(REPEAT); sampler.setWrapT(REPEAT); }
  }

  viewer.addEventListener('load', function () {
    var material = viewer.model.materials[0];
    var pbr = material.pbrMetallicRoughness;
    packed([maps.occlusion, maps.roughness, maps.metallic]).then(function (orm) {
      return Promise.all([viewer.createTexture(maps.albedo), viewer.createTexture(maps.normal), viewer.createTexture(orm)]);
    }).then(function (textures) {
      put(pbr.baseColorTexture, textures[0]);
      put(material.normalTexture, textures[1]);
      put(pbr.metallicRoughnessTexture, textures[2]);
      put(material.occlusionTexture, textures[2]);
      viewer.classList.add('is-dressed');
    }).catch(function (err) {
      if (note) note.textContent = 'The maps could not be put on the sphere: ' + err.message;
    });
  }, { once: true });
  viewer.addEventListener('error', function () {
    if (note) note.textContent = 'The sphere could not be shown in this browser.';
  });
})();
