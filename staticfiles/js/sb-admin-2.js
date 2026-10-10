(function($) {
  "use strict"; // Start of use strict

  var mobileBreakpoint = 768;
  var $body = $("body");
  var $sidebar = $(".sidebar");

  function isMobileSidebar() {
    return $(window).width() < mobileBreakpoint;
  }

  function ensureSidebarBackdrop() {
    if (!$(".sidebar-backdrop").length) {
      $body.append('<div class="sidebar-backdrop" aria-hidden="true"></div>');
    }
  }

  function setToggleState(isOpen) {
    $("#sidebarToggleTop").attr("aria-expanded", isOpen ? "true" : "false");
  }

  function hideSidebarMenus() {
    var $menus = $(".sidebar .collapse");

    if ($.fn.collapse) {
      $menus.collapse("hide");
    }

    $menus.removeClass("show collapsing").addClass("collapse").css("display", "none");
    $('.sidebar [data-toggle="collapse"]').addClass("collapsed").attr("aria-expanded", "false");
  }

  function closeMobileSidebar() {
    $body.addClass("sidebar-toggled").removeClass("sidebar-mobile-open");
    $sidebar.addClass("toggled");
    hideSidebarMenus();
    setToggleState(false);
  }

  function openMobileSidebar() {
    ensureSidebarBackdrop();
    $body.removeClass("sidebar-toggled").addClass("sidebar-mobile-open");
    $sidebar.removeClass("toggled");
    setToggleState(true);
  }

  function syncSidebarLayout() {
    if (isMobileSidebar()) {
      ensureSidebarBackdrop();
      if (!$sidebar.hasClass("toggled")) {
        openMobileSidebar();
      } else {
        closeMobileSidebar();
      }
    } else {
      $body.removeClass("sidebar-mobile-open");
      setToggleState(false);
    }
  }

  function setActiveSidebarItem() {
    var currentPath = window.location.pathname.replace(/\/+$/, "") || "/";

    $(".sidebar a[href]").each(function() {
      var linkPath;

      try {
        linkPath = new URL(this.getAttribute("href"), window.location.origin).pathname;
      } catch (error) {
        return;
      }

      linkPath = linkPath.replace(/\/+$/, "") || "/";
      if (linkPath !== currentPath) {
        return;
      }

      var $link = $(this);
      var $collapse = $link.closest(".collapse");

      $link.addClass("active");

      if ($collapse.length) {
        $collapse.addClass("show");
        $('[data-target="#' + $collapse.attr("id") + '"]')
          .removeClass("collapsed")
          .attr("aria-expanded", "true")
          .closest(".nav-item")
          .addClass("active");
      } else {
        $link.closest(".nav-item").addClass("active");
      }
    });
  }

  function setupSidebarFallbackCollapse() {
    if ($.fn.collapse) {
      return;
    }

    $(document).on("click", ".sidebar [data-toggle='collapse']", function(e) {
      var $trigger = $(this);
      var targetSelector = $trigger.attr("data-target") || $trigger.attr("href");
      var $target = $(targetSelector);

      if (!$target.length) {
        return;
      }

      e.preventDefault();

      if ($target.hasClass("show")) {
        $target.removeClass("show").css("display", "none");
        $trigger.addClass("collapsed").attr("aria-expanded", "false");
        return;
      }

      $target
        .siblings(".collapse.show")
        .removeClass("show")
        .css("display", "none")
        .each(function() {
          $('.sidebar [data-target="#' + this.id + '"]').addClass("collapsed").attr("aria-expanded", "false");
        });

      $target.addClass("show").css("display", "block");
      $trigger.removeClass("collapsed").attr("aria-expanded", "true");
    });
  }

  setActiveSidebarItem();
  setupSidebarFallbackCollapse();

  if (isMobileSidebar()) {
    closeMobileSidebar();
  } else {
    syncSidebarLayout();
  }

  // Toggle the side navigation
  $("#sidebarToggle, #sidebarToggleTop").on('click', function(e) {
    e.preventDefault();

    if (isMobileSidebar()) {
      if ($sidebar.hasClass("toggled")) {
        openMobileSidebar();
      } else {
        closeMobileSidebar();
      }
      return;
    }

    $body.toggleClass("sidebar-toggled");
    $sidebar.toggleClass("toggled");
    if ($sidebar.hasClass("toggled")) {
      hideSidebarMenus();
    };
  });

  // Close any open menu accordions when window is resized below 768px
  $(window).resize(function() {
    syncSidebarLayout();
  });

  $(document).on("click", ".sidebar-backdrop", closeMobileSidebar);

  $(document).on("keydown", function(e) {
    if (e.key === "Escape" && isMobileSidebar() && $body.hasClass("sidebar-mobile-open")) {
      closeMobileSidebar();
    }
  });

  $(document).on("click", ".sidebar a[href]:not([href='#']):not([data-toggle='collapse'])", function() {
    if (isMobileSidebar()) {
      closeMobileSidebar();
    }
  });

  // Prevent the content wrapper from scrolling when the fixed side navigation hovered over
  $('body.fixed-nav .sidebar').on('mousewheel DOMMouseScroll wheel', function(e) {
    if ($(window).width() > 768) {
      var e0 = e.originalEvent,
        delta = e0.wheelDelta || -e0.detail;
      this.scrollTop += (delta < 0 ? 1 : -1) * 30;
      e.preventDefault();
    }
  });

  // Scroll to top button appear
  $(document).on('scroll', function() {
    var scrollDistance = $(this).scrollTop();
    if (scrollDistance > 100) {
      $('.scroll-to-top').fadeIn();
    } else {
      $('.scroll-to-top').fadeOut();
    }
  });

  // Smooth scrolling using jQuery easing
  $(document).on('click', 'a.scroll-to-top', function(e) {
    var $anchor = $(this);
    $('html, body').stop().animate({
      scrollTop: ($($anchor.attr('href')).offset().top)
    }, 1000, 'easeInOutExpo');
    e.preventDefault();
  });

})(jQuery); // End of use strict
